#!/usr/bin/python3

import os
import telegram
import threading
import logging
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from httplib2 import Http
from oauth2client import file, client, tools


class Notification:

  tbot = None
  gdrive = None

  def __init__(self):
    self.tbot = telegram.Bot(os.getenv('TELEGRAM_TOKEN'))
    self.gdrive = self.setup_gdrive()

  def setup_gdrive(self):
      # Setup the Drive v3 API
    SCOPES = 'https://www.googleapis.com/auth/drive.file'
    store = file.Storage('token.json')
    credentials = store.get()
    if not credentials or credentials.invalid:
      flow = client.flow_from_clientsecrets('credentials.json', SCOPES)
      args = tools.argparser.parse_args()
      args.noauth_local_webserver = True
      credentials = tools.run_flow(flow, store, args)
    service = build('drive', 'v3', http=credentials.authorize(
        Http()), cache_discovery=False)
    return service

  def notify_image(self, image):
    try:
      self.send_image(image)
    except Exception as e:
      logging.error('While sending telegram image: '+e)

  def notify_video(self, video):
    try:
      uploaded = self.upload_video(video)
      self.send_message(uploaded)
    except Exception as e:
      logging.error('While uploading video: '+e)

  def upload_video(self, path):
    # Find folders
    results = self.gdrive.files().list(pageSize=100, fields="files(id, name)",
                                       q="mimeType = 'application/vnd.google-apps.folder' AND trashed != true").execute()
    files = results.get('files', [])
    # Find PiCamera folder
    folder = next(x for x in files if x['name'] == os.getenv('GOOGLE_DRIVE_DIR'))
    if not folder:
      # Create PiCamera folder if not exists
      body = {'name': os.getenv('GOOGLE_DRIVE_DIR'),
              'mimeType': 'application/vnd.google-apps.folder'}
      folder = self.gdrive.files().create(body=body, fields='id, name').execute()

    # Upload file
    media = MediaFileUpload(path, mimetype='video/mp4')
    body = {'name': os.path.basename(path), 'parents': [folder.get('id')]}
    uploaded = self.gdrive.files().create(
        body=body, fields='id, name, webViewLink, webContentLink', media_body=media).execute()

    return uploaded

  def send_message(self, uploaded):
    self.tbot.send_message(os.getenv('TELEGRAM_CHAT_ID'),
                           'View Video: ' + uploaded.get('webViewLink'))

  def send_image(self, image):
    self.tbot.send_photo(os.getenv('TELEGRAM_CHAT_ID'),
                         photo=open(image, 'rb'), caption='Motion detected')
