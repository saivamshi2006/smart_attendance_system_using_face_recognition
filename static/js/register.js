const startCameraBtn = document.getElementById('startCameraBtn');
const captureBtn = document.getElementById('captureBtn');
const stopCameraBtn = document.getElementById('stopCameraBtn');
const video = document.getElementById('video');
const canvas = document.getElementById('canvas');
const capturedImageInput = document.getElementById('capturedImageInput');
let stream = null;

async function startCamera() {
    try {
        stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        video.srcObject = stream;
        captureBtn.disabled = false;
        stopCameraBtn.disabled = false;
        startCameraBtn.disabled = true;
    } catch (error) {
        alert('Unable to access the camera. Please allow camera permissions.');
    }
}

function stopCamera() {
    if (stream) {
        stream.getTracks().forEach(track => track.stop());
        stream = null;
    }
    video.srcObject = null;
    captureBtn.disabled = true;
    stopCameraBtn.disabled = true;
    startCameraBtn.disabled = false;
}

function captureImage() {
    if (!stream) {
        alert('Camera is not active.');
        return;
    }

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const context = canvas.getContext('2d');
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL('image/jpeg');
    capturedImageInput.value = dataUrl;
    alert('Captured image is ready to submit.');
}

startCameraBtn.addEventListener('click', startCamera);
captureBtn.addEventListener('click', captureImage);
stopCameraBtn.addEventListener('click', stopCamera);
window.addEventListener('beforeunload', stopCamera);
