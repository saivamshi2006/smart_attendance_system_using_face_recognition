/**
 * Teacher attendance: auto-start webcam on load; single "Submit attendance" captures
 * the current video frame into the hidden captured_image field (or uses optional file).
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
        'Unable to access the camera. Allow permissions in the browser, or use the optional image upload.'
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
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, w, h);
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
      const hasFile =
        fileInput &&
        fileInput.files &&
        fileInput.files.length > 0 &&
        fileInput.files[0].size > 0;

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
        'Could not capture from the camera. Wait for the video to appear, or use the optional image upload.'
      );
    });
  }

  window.addEventListener('beforeunload', stopCamera);
  startCamera();
})();
