#!/usr/bin/python3

import os
import telegram
import traceback
import threading
import logging
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from httplib2 import Http
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

class Notification:

  tbot = None
  gdrive = None

  def __init__(self):
    self.tbot = None
    if os.getenv('PYCAM_TELEGRAM_TOKEN'):
      self.tbot = telegram.Bot(os.getenv('PYCAM_TELEGRAM_TOKEN'))

    self.gdrive = self.setup_gdrive()

  def setup_gdrive(self):
    # Setup the Drive v3 API
    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    service = build('drive', 'v3', credentials=creds, cache_discovery=False)
    return service

  def notify_image(self, image):
    try:
      self.send_image(image)
    except Exception as e:
      logging.error('While sending telegram image: %s' % e)
      traceback.print_exc()


  def notify_video(self, video):
    try:
      uploaded = self.upload_video(video)
      self.send_message(uploaded)
    except Exception as e:
      logging.error('While uploading video: %s' % e)
      traceback.print_exc()

  def upload_video(self, path):
    # Find folders
    results = self.gdrive.files().list(pageSize=100, fields="files(id, name)",
                                       q="mimeType = 'application/vnd.google-apps.folder' AND trashed != true").execute()
    files = results.get('files', [])
    # Find PiCamera folder
    try:
      folder = next(x for x in files if x['name'] == os.getenv('PYCAM_GOOGLE_DRIVE_DIR'))
    except StopIteration:
      # Create PiCamera folder if not exists
      body = {'name': os.getenv('PYCAM_GOOGLE_DRIVE_DIR'),
              'mimeType': 'application/vnd.google-apps.folder'}
      folder = self.gdrive.files().create(body=body, fields='id, name').execute()

    # Upload file
    media = MediaFileUpload(path, mimetype='video/mp4')
    body = {'name': os.path.basename(path), 'parents': [folder.get('id')]}
    uploaded = self.gdrive.files().create(
        body=body, fields='id, name, webViewLink, webContentLink', media_body=media).execute()

    return uploaded

  def send_message(self, uploaded):
    if self.tbot:
      self.tbot.send_message(os.getenv('PYCAM_TELEGRAM_CHAT_ID'),
                             'View Video: ' + uploaded.get('webViewLink'))

  def send_image(self, image):
    if self.tbot:
      self.tbot.send_photo(os.getenv('PYCAM_TELEGRAM_CHAT_ID'),
                           photo=open(image, 'rb'), caption='Motion detected')
