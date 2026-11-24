"""Run the DJ History workflow against a configured Drive folder and workbook."""

import logging
import os

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from google_outputs import DriveFolder, HistorySheet
from workflow import run_workflows


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        scopes = ["https://www.googleapis.com/auth/drive",
                  "https://www.googleapis.com/auth/spreadsheets"]
        credentials = Credentials.from_service_account_file(
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"], scopes=scopes
        )
        with AuthorizedSession(credentials, refresh_timeout=15) as session:
            drive = DriveFolder(session, os.environ["DJ_SET_DRIVE_FOLDER_ID"])
            history = HistorySheet(session, os.environ["DJ_SET_SPREADSHEET_ID"])
            return run_workflows(list(drive.files()), drive.download, drive.rename, {"DH": history})
    except Exception as error:
        logging.error("Job setup or Drive listing failed (%s)", type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
