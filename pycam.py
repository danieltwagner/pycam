#!/usr/bin/python3

import os
from dotenv import load_dotenv
current_dir = os.path.dirname(os.path.realpath(__file__))
load_dotenv(verbose=True, dotenv_path=os.path.join(current_dir, '.env'))

import logging
import threading
from motion_recorder import MotionRecorder
from notification import Notification


logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)-8s %(message)s')

notification = Notification()


def watch_captures(captures):
  while True:
    video = captures.get()
    logging.info("motion capture in '{0}'".format(video))
    notification.notify_video(video)
    os.remove(video)
    captures.task_done()


def watch_images(images):
  while True:
    image = images.get()
    logging.info("image capture in '{0}'".format(image))
    notification.notify_image(image)
    os.remove(image)
    images.task_done()


try:
  with MotionRecorder() as mr:
    mr.start()
    captures = threading.Thread(
        name="watch_captures", target=watch_captures, daemon=True, args=[mr.captures])
    images = threading.Thread(name="watch_images",
                              target=watch_images, daemon=True, args=[mr.images])
    captures.start()
    images.start()
    captures.join()
    images.join()

except (KeyboardInterrupt, SystemExit):
  exit()
