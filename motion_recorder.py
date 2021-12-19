#!/usr/bin/python3

import io
import os
import threading
import picamera
import picamera.array
import queue
import signal
import logging
import time
import numpy as np
from PIL import Image
from motion_vector_reader import MotionVectorReader

current_dir = os.path.dirname(os.path.realpath(__file__))


class MotionRecorder(threading.Thread):
  """Record video into a circular memory buffer and extract motion vectors for
  simple motion detection analysis. Enables dumping the video frames out to file
  if motion is detected.
  """

  # half of hardware resolution leaves us HD 4:3 and provides 2x2 binning
  # for V1 camera: 1296x972, for V2 camera: 1640x1232. Also use sensor_mode: 4
  width = int(os.getenv('PYCAM_WIDTH'))
  height = int(os.getenv('PYCAM_HEIGHT'))
  framerate = int(os.getenv('PYCAM_FPS'))  # lower framerate for more time on per-frame analysis
  bitrate = int(os.getenv('PYCAM_BITRATE_KBPS')) * 1000  # 2Mbps is a high quality stream for 10 fps HD video
  prebuffer = int(os.getenv('PYCAM_PREBUFFER_SEC'))  # number of seconds to keep in buffer
  postbuffer = int(os.getenv('PYCAM_POSTBUFFER_SEC'))  # number of seconds to record post end of motion
  overlay = bool(int(os.getenv('PYCAM_OVERLAY')))
  capture_still = bool(int(os.getenv('PYCAM_JPEG')))
  video_dir = os.path.join(current_dir, 'videos')
  image_dir = os.path.join(current_dir, 'images')
  video_file_pattern = '%Y-%m-%d_%H-%M-%S'  # filename pattern for time.strfime
  image_file_pattern = '%Y-%m-%d_%H-%M-%S'  # filename pattern for time.strfime
  rotation = int(os.getenv('PYCAM_ROTATION'))
  # number of connected MV blocks (each 16x16 pixels) to count as a moving object
  _area = int(os.getenv('PYCAM_DETECT_BLOCKS'))
  _frames = int(os.getenv('PYCAM_DETECT_FRAMES'))  # number of frames which must contain movement to trigger

  _camera = None
  _motion = None
  _output = None

  captures = queue.Queue()
  images = queue.Queue()

  def __enter__(self):
    self.start_camera()
    threading.Thread(name="blink", target=self.blink, daemon=True).start()
    threading.Thread(name="annotate", target=self.annotate_with_datetime, args=(
        self._camera,), daemon=True).start()
    if self.overlay:
      threading.Thread(name="motion overlay",
                       target=self.motion_overlay, daemon=True).start()
    logging.info("now ready to detect motion")
    return self

  def __exit__(self, type, value, traceback):
    camera = self._camera
    if camera.recording:
      camera.stop_recording()

  def __init__(self, overlay=False):
    super().__init__()
    self.overlay = overlay

  def __str__(self):
    if (self._motion):
      return str(self._motion)

  def wait(self, timeout=0.0):
    """Use this instead of time.sleep() from sub-threads so that they would
    wake up to exit quickly when instance is being shut down.
    """
    try:
      self._camera.wait_recording(timeout)
    except picamera.exc.PiCameraNotRecording:
      # that's fine, return immediately
      pass

  @property
  def area(self):
    return self._area

  @area.setter
  def area(self, value):
    self._area = value
    if self._motion:
      self._motion.area = value

  @property
  def frames(self):
    return self._frames

  @frames.setter
  def frames(self, value):
    self._frames = value
    if self._motion:
      self._motion.frames = value

  def start_camera(self):
    """Sets up PiCamera to record H.264 High/4.1 profile video with enough
    intra frames that there is at least one in the in-memory circular buffer when
    motion is detected."""
    self._camera = camera = picamera.PiCamera(clock_mode='raw', sensor_mode=4,
                                              resolution=(self.width, self.height), framerate=self.framerate)
    camera.rotation = self.rotation
    #camera.sensor_mode = 4
    if self.overlay:
      camera.start_preview(alpha=255)
    self._stream = stream = picamera.PiCameraCircularIO(
        camera, seconds=self.prebuffer+1, bitrate=self.bitrate)
    self._motion = motion = MotionVectorReader(
        camera, window=self.postbuffer*self.framerate, area=self.area, frames=self.frames)
    camera.start_recording(stream, motion_output=motion,
                           format='h264', profile='high', level='4.1', bitrate=self.bitrate,
                           inline_headers=True, intra_period=self.prebuffer*self.framerate // 2)
    camera.wait_recording(1)  # give camera some time to start up

  def capture_jpeg(self):
    name = time.strftime(self.image_file_pattern)
    tmp_path = os.path.join(self.image_dir, name+'-temp.jpg')
    path = os.path.join(self.image_dir, name+'.jpg')
    self._camera.capture(tmp_path, use_video_port=True, format='jpeg', quality=100)
    img = Image.open(tmp_path)
    img.save(path, 'JPEG', quality=75)
    os.remove(tmp_path)
    return path

  def run(self):
    """Main loop of the motion recorder. Waits for trigger from the motion detector
    async task and writes in-memory circular buffer to file every time it happens,
    until motion detection trigger. After each recording, the name of the file
    is posted to captures queue, where whatever is consuming the recordings can
    pick it up.
    """
    self._motion.disabled = False
    while self._camera.recording:
      # wait for motion detection
      if self._motion.wait(self.prebuffer):
        if self._motion.motion():
          self._camera.led = True
          logging.info("Detected motion")
          try:
            # start a new video, then append circular buffer to it until
            # motion ends
            name = time.strftime(self.video_file_pattern)
            path = os.path.join(self.video_dir, name+'.h264')
            output = io.open(path, 'wb')
            self.append_buffer(output, header=True)

            # Capture image in the beginning of motion
            if self.capture_still:
              self.images.put(self.capture_jpeg())

            while self._motion.motion() and self._camera.recording:
              self.wait(self.prebuffer / 2)
              self.append_buffer(output)

          except picamera.PiCameraError as e:
            logging.error("while saving recording: "+e)

          finally:
            output.close()
            self._output = None
            self._camera.led = False

            # Wrap h264 in mkv container with appropriate fps
            mkvpath = os.path.join(self.video_dir, name+'.mkv')
            os.system('ffmpeg -r '+str(self.framerate)+' -i '+path +
                      ' -vcodec copy '+mkvpath+' >/dev/null 2>&1')
            os.remove(path)  # Delete original .h264 file
            self.captures.put(mkvpath)

          # wait for the circular buffer to fill up before looping again
          self.wait(self.prebuffer / 2)

  def append_buffer(self, output, header=False):
    """Flush contents of circular framebuffer to current on-disk recording.
    """
    if header:
      header = picamera.PiVideoFrameType.sps_header
    else:
      header = None
    stream = self._stream
    with stream.lock:
      stream.copy_to(output, seconds=self.prebuffer, first_frame=header)
      #firstframe = lastframe = next(iter(stream.frames)).index
      #for frame in stream.frames: lastframe = frame.index
      #logging.debug("write {0} to {1}".format(firstframe,lastframe))
      stream.clear()
    return output

  def blink(self):
    """Background thread for blinking the camera LED (to signal detection).
    """
    while self._camera.recording:
      if not self._motion.motion() and self._output is None:
        self._camera.led = True
        self.wait(0.05)  # this is enough for a quick blink
        self._camera.led = False
      self.wait(2-time.time() % 2)  # wait up to two seconds

  def annotate_with_datetime(self, camera):
    """Background thread for annotating date and time to video.
    """
    while camera.recording:
      camera.annotate_text = time.strftime("%y-%m-%d %H:%M") + " " + str(self)
      camera.annotate_background = True
      self.wait(60-time.gmtime().tm_sec)  # wait to beginning of minute

  def motion_overlay(self):
    """Background thread for drawing motion detection mask to on-screen preview.
    Basically for debug purposes.
    """
    width = (self.width//16//32+1) * \
        32  # MV blocks rounded up to next-32 pixels
    # MV blocks rounded up to next-16 pixels
    height = (self.height//16//16+1)*16
    buffer = np.zeros((height, width, 3), dtype=np.uint8)
    logging.debug(
        "creating a motion overlay of size {0}x{1}".format(width, height))
    overlay = self._camera.add_overlay(
        memoryview(buffer), size=(width, height), alpha=128)
    # this thread will exit immediately if motion overlay is configured off
    while self._camera.recording:
      a = self._motion.field  # last processed MV frame
      if a is not None:
        # center MV array on output buffer
        w = a.shape[1]
        x = (width-w)//2+1
        h = a.shape[0]
        y = (height-h)//2+1
        # highlight those blocks which exceed thresholds on green channel
        buffer[y:y+h, x:x+w, 1] = a * 255
      try:
        overlay.update(memoryview(buffer))
      except picamera.exc.PiCameraRuntimeError as e:
        # it's possible to get a "failed to get a buffer from the pool" error here
        pass
      self.wait(0.5)  # limit the preview framerate to max 2 fps
    self._camera.remove_overlay(overlay)
