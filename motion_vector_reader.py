import threading
import picamera
import picamera.array
import logging
import numpy as np
from scipy import ndimage
from collections import deque

# a no-op to handle the profile decorator -- or uncomment the profile import below
def profile(func):
  def func_wrapper(*args, **kwargs):
    return func(*args, **kwargs)
  return func_wrapper

#from profilehooks import profile

class MotionVectorReader(picamera.array.PiMotionAnalysis):
  """This is a hardware-assisted motion detector, able to process a high-definition
  video stream (but not full HD, and at 10 fps only) on a Raspberry Pi 1, despite
  being implemented in Python. How is that possible?! The magic is in computing
  from H.264 motion vector data only: the Pi camera outputs 16x16 macro block MVs,
  so we only have about 5000 blocks per frame to process. Numpy is fast enough for that.
  """

  area = 0
  frames = 0
  window = 0
  camera = None
  trigger = threading.Event()
  output = None

  def __str__(self):
    return "sensitivity {0}/{1}".format(self.area, self.frames)

  def __init__(self, camera, window=10, area=25, frames=4):
    """Initialize motion vector reader

    Parameters
    ----------
    camera : PiCamera
    size : minimum number of connected MV blocks (each 16x16 pixels) to qualify for movement
    frames : minimum number of frames to contain movement to quality
    """
    super(type(self), self).__init__(camera)
    self.camera = camera
    self.area = area
    self.frames = frames
    self.window = window
    self._last_frames = deque(maxlen=window)
    logging.debug("motion detection sensitivity: "+str(self))

  def save_motion_vectors(self, file):
    self.output = open(file, "ab")

  def set(self):
    self.trigger.set()

  def clear(self):
    self.trigger.clear()

  def motion(self):
    return self.trigger.is_set()

  def wait(self, timeout=0.0):
    return self.trigger.wait(timeout)

  disabled = False
  _last_frames = deque(maxlen=10)
  noise = None

  @profile
  def analyse(self, a):
    """Runs once per frame on a 16x16 motion vector block buffer (about 5000 values).
    Must be faster than frame rate (max 100 ms for 10 fps stream).
    Sets self.trigger Event to trigger capture.
    """

    if self.disabled:
      self._last_frames.append(False)
      return

    import struct
    if self.output:
      self.output.write(struct.pack('>8sL?8sBBB',
                                    b'frameno\x00', self.camera.frame.index, self.motion(),
                                    b'mvarray\x00', a.shape[0], a.shape[1], a[0].itemsize))
      self.output.write(a)

    # the motion vector array we get from the camera contains three values per
    # macroblock: the X and Y components of the inter-block motion vector, and
    # sum-of-differences value. the SAD value has a completely different meaning
    # on a per-frame basis, but abstracted over a longer timeframe in a mostly-still
    # video stream, it ends up estimating noise pretty well. Accordingly, we
    # can use it in a decay function to reduce sensitivity to noise on a per-block
    # basis

    # accumulate and decay SAD field
    noise = self.noise
    if not noise:
      noise = np.zeros(a.shape, dtype=np.short)
    shift = max(self.window.bit_length()-2, 0)
    noise -= (noise >> shift) + 1  # decay old noise
    noise = np.add(noise, a['sad'] >> shift).clip(0)

    # then look for motion vectors exceeding the length of the current mask
    a = np.sqrt(
        np.square(a['x'].astype(np.float)) +
        np.square(a['y'].astype(np.float))
    ).clip(0, 255).astype(np.uint8)
    self.field = a

    # look for the largest continuous area in picture that has motion
    # every motion vector exceeding current noise field
    mask = (a > (noise >> 4))
    labels, count = ndimage.label(mask)  # label all motion areas
    # number of MV blocks per area
    sizes = ndimage.sum(mask, labels, range(count + 1))
    largest = np.sort(sizes)[-1]  # what's the size of the largest area

    # Do some extra work to clean up the preview overlay. Remove all but the largest
    # motion region, and even that if it's just one MV block (considered noise)
    #mask = (sizes < max(largest,2))
    # mask = mask[labels] # every part of the image except for the largest object
    #self.field = mask

    # TODO: all the regions (and small movement) that we discarded as non-essential:
    # should feed that to a subroutine that weights that kind of movement out of the
    # picture in the future for auto-adaptive motion detector

    # does that area size exceed the minimum motion threshold?
    motion = (largest >= self.area)
    # then consider motion repetition
    self._last_frames.append(motion)

    def count_longest(a, value):
      ret = i = 0
      while i < len(a):
        for j in range(0, len(a)-i):
          if a[i+j] != value:
            break
          ret = max(ret, j+1)
        i += j+1
      return ret
    longest_motion_sequence = count_longest(self._last_frames, True)

    if longest_motion_sequence >= self.frames:
      self.set()
    elif longest_motion_sequence < 1:
      # clear motion flag once motion has ceased entirely
      self.clear()
    return motion
