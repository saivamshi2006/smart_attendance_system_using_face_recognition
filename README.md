# Face Recognition Attendance Tracking System

## Overview
A web-based attendance tracker built with Flask, OpenCV, SQLite, and JavaScript. Users can register with iris images or webcam capture, then mark attendance via webcam capture or image upload.

## Project Structure
- `app.py` - Flask backend with routes, database logic, and attendance workflows.
- `face_utils.py` - Face image utilities, base64 decoding, and face encoding extraction.
- `app.db` - SQLite database file generated automatically at runtime.
- `requirements.txt` - Python dependencies.
- `templates/` - HTML pages for home, register, attendance, and dashboard.
- `static/css/style.css` - Frontend styling.
- `static/js/register.js` - Camera capture logic for registration.
- `static/js/attendance.js` - Camera capture logic for attendance.

## Setup
1. Open a terminal in the project folder.
2. Create a virtual environment (recommended):
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

> Note: This project uses OpenCV for iris detection and does not require `face_recognition` or `dlib`.

## Run the Application
```powershell
python app.py
```

Then open `http://127.0.0.1:5000` in your browser.

## Usage
1. Go to the Register page.
2. Enter a unique ID and name.
3. Upload one or more face images, or capture a face using the webcam.
4. Go to the Attendance page.
5. Upload an image or capture the face with your webcam.
6. The system will match the face and store attendance once per user per day.
7. View all records in the Dashboard or download a CSV file.

## Notes
- If no face is detected, the app returns an error message.
- If multiple faces are detected, the app requests a single-face image.
- Duplicate daily attendance entries are prevented.
