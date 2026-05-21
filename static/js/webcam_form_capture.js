/**
 * Shared behavior for forms that need a webcam frame on submit (e.g. admin add student).
 * Same flow as attendance_section.js: auto-start camera; optional file bypasses capture.
 */
(function () {
  const video = document.getElementById('video');
  const canvas = document.getElementById('canvas');
  const capturedInput = document.getElementById('capturedImageInput');
  const cameraError = document.getElementById('cameraError');
  const form = document.getElementById('mainForm');
  const fileInput = document.getElementById('optionalImageUpload');

  let stream = null;

  function showCameraError(msg) {
    if (!cameraError) return;
    cameraError.textContent = msg;
    cameraError.hidden = false;
  }

  async function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showCameraError('Camera is not available in this browser.');
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user' },
        audio: false,
      });
      video.srcObject = stream;
    } catch (err) {
      showCameraError(
        'Unable to access the camera. Allow permissions, upload images, or try another browser.'
      );
    }
  }

  function captureFrameToHidden() {
    if (!stream || !video || !canvas || !capturedInput) return false;
    const w = video.videoWidth;
    const h = video.videoHeight;
    if (!w || !h) return false;
    canvas.width = w;
    canvas.height = h;
    canvas.getContext('2d').drawImage(video, 0, 0, w, h);
    capturedInput.value = canvas.toDataURL('image/jpeg', 0.92);
    return true;
  }

  function stopCamera() {
    if (stream) {
      stream.getTracks().forEach(function (t) {
        t.stop();
      });
      stream = null;
    }
    if (video) video.srcObject = null;
  }

  if (form) {
    form.addEventListener('submit', function (e) {
      let hasFile = false;
      if (fileInput && fileInput.files && fileInput.files.length) {
        for (let i = 0; i < fileInput.files.length; i += 1) {
          if (fileInput.files[i].size > 0) {
            hasFile = true;
            break;
          }
        }
      }

      if (hasFile) {
        capturedInput.value = '';
        stopCamera();
        return;
      }

      if (captureFrameToHidden()) {
        stopCamera();
        return;
      }

      e.preventDefault();
      showCameraError(
        'Could not capture from the camera. Wait for the preview, or add optional image uploads.'
      );
    });
  }

  window.addEventListener('beforeunload', stopCamera);
  startCamera();
})();
