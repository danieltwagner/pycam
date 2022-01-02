#!/usr/bin/python3

import os
import datetime
import telegram
import traceback
import threading
import logging
import dropbox
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

class Notification:

  def __init__(self):
    self.tbot = None
    self.gdrive = None
    self.dbx = None

    if os.getenv('PYCAM_TELEGRAM_TOKEN'):
      self.tbot = telegram.Bot(os.getenv('PYCAM_TELEGRAM_TOKEN'))

    if int(os.getenv('PYCAM_UPLOAD_GDRIVE')):
      self.gdrive = self.setup_gdrive()

    if int(os.getenv('PYCAM_UPLOAD_DBX')):
      self.dbx = self.setup_dbx()

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

  def setup_dbx(self):
    TOKEN_PATH = 'token_dbx.txt'
    with open(TOKEN_PATH, 'r') as f:
      token = f.read().strip()
    return dropbox.Dropbox(token)

  def notify_image(self, image):
    try:
      self.send_image(image)
    except Exception as e:
      logging.error('While sending telegram image: %s' % e)
      traceback.print_exc()


  def notify_video(self, video):
    try:
      link = self.upload_video(video)
      self.send_message(link)
    except Exception as e:
      logging.error('While uploading video: %s' % e)
      traceback.print_exc()

  def upload_video(self, path):
    if self.gdrive:
      # Find folders
      results = self.gdrive.files().list(pageSize=100, fields="files(id, name)",
                                         q="mimeType = 'application/vnd.google-apps.folder' AND trashed != true").execute()
      files = results.get('files', [])
      # Find PiCamera folder
      try:
        folder = next(x for x in files if x['name'] == os.getenv('PYCAM_UPLOAD_DIR'))
      except StopIteration:
        # Create PiCamera folder if not exists
        body = {'name': os.getenv('PYCAM_UPLOAD_DIR'),
                'mimeType': 'application/vnd.google-apps.folder'}
        folder = self.gdrive.files().create(body=body, fields='id, name').execute()

      # Upload file
      media = MediaFileUpload(path, mimetype='video/mp4')
      body = {'name': os.path.basename(path), 'parents': [folder.get('id')]}
      uploaded = self.gdrive.files().create(
          body=body, fields='id, name, webViewLink, webContentLink', media_body=media).execute()

      return uploaded.get('webViewLink')

    elif self.dbx:
      folder = '/%s/%s' % (os.getenv('PYCAM_UPLOAD_DIR'), datetime.date.today())
      try:
        self.dbx.files_get_metadata(folder)
      except:
        self.dbx.files_create_folder_v2(folder)

      with open(path, 'rb') as f:
        self.dbx.files_upload(f.read(), "%s/%s" % (folder, os.path.basename(path)))

  def send_message(self, link):
    if self.tbot:
      self.tbot.send_message(os.getenv('PYCAM_TELEGRAM_CHAT_ID'),
                             'View Video: ' + link)

  def send_image(self, image):
    if self.tbot:
      self.tbot.send_photo(os.getenv('PYCAM_TELEGRAM_CHAT_ID'),
                           photo=open(image, 'rb'), caption='Motion detected')
