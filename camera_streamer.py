# Code builds on the example provided by the docs
# http://picamera.readthedocs.io/en/latest/recipes2.html#web-streaming

import io
import picamera
import picamera.array
import logging
import socketserver
from threading import Condition, Thread
from http import server
import ssl
import datetime
import time
import numpy as np
import subprocess

IS_HTTP = True # False uses HTTPS

is_movement_detected = False

motion_dtype = np.dtype([
    ('x', 'i1'),
    ('y', 'i1'),
    ('sad', 'u2'),
    ])

class MyMotionDetector(object):
    def __init__(self, camera):
        width, height = camera.resolution
        self.cols = (width + 15) // 16
        self.cols += 1
        self.rows = (height + 15) // 16
        # masks to check for movement in a subsection of frame
        #self.mask = np.zeros((self.rows, self.cols), dtype=np.bool)
        #self.mask[:,:] = False
        #self.mask[6:,8:32] = True

    def write(self, s):
        global is_movement_detected
        data = np.fromstring(s, dtype=motion_dtype)
        data = data.reshape((self.rows, self.cols))
        data = np.sqrt(
            np.square(data['x'].astype(np.float)) +
            np.square(data['y'].astype(np.float))
            ).clip(0, 255).astype(np.uint8)
        # If there're more than 6 vectors with a magnitude greater
        # than 20, then motion is detected
        if (data > 20).sum() > 6:
            is_movement_detected = True
        else:
            is_movement_detected = False
        return len(s)

PAGE="""\
<html>
<head>
<title>Raspberry Pi - Surveillance Camera</title>
</head>
<body bgcolor="black">
<center><h1 style="color:DodgerBlue;">Raspberry Pi - Surveillance Camera</h1></center>
<center><img src="stream.mjpg" width="640" height="480"></center>
</body>
</html>
"""

class StreamingOutput(object):
    def __init__(self):
        self.frame = None
        self.buffer = io.BytesIO()
        self.condition = Condition()

    def write(self, buf):
        if buf.startswith(b'\xff\xd8'):
            # New frame, copy the existing buffer's content and notify all
            # clients it's available
            self.buffer.truncate()
            with self.condition:
                self.frame = self.buffer.getvalue()
                self.condition.notify_all()
            self.buffer.seek(0)
        return self.buffer.write(buf)

class StreamingHandler(server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
        elif self.path == '/index.html':
            content = PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == '/stream.mjpg':
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                logging.warning(
                    'Removed streaming client %s: %s',
                    self.client_address, str(e))
        else:
            self.send_error(404)
            self.end_headers()

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def annotate_text(cam):
    while True:
        time.sleep(1)
        cam.annotate_text = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def motion_detector_wait():
    while True:
        camera.wait_recording(1, splitter_port=2)
        if is_movement_detected:
            now = datetime.datetime.now().strftime('%Y-%m-%dT%H.%M.%S')
            filename = '/media/pi/STORAGE/motion/{}_After.h264'.format(now)
            camera.split_recording(filename, splitter_port=2)
            stream.copy_to(filename.replace('After', 'Before'), seconds=10, first_frame=picamera.PiVideoFrameType.sps_header)
            no_movement_counter = 0
            while is_movement_detected or no_movement_counter < 15:
                time.sleep(1)
                no_movement_counter = no_movement_counter + 1 if not is_movement_detected else 0
            subprocess.call('cat "{1}" "{0}" > "{2}" && rm -f "{0}" "{1}" &'.format(filename, filename.replace('After', 'Before'), filename.replace('After', 'Final')), shell=True)
            stream.clear()
            camera.split_recording(stream, splitter_port=2)

with picamera.PiCamera(resolution='640x480', framerate=24) as camera:
    output = StreamingOutput()
    camera.rotation = 90
    camera.annotate_background = picamera.Color(y=0.1, u=0, v=0)
    camera.annotate_text_size = 14
    stream = picamera.PiCameraCircularIO(camera, seconds=10, splitter_port=2)
    thread = Thread(target=annotate_text, args=(camera, ))
    camera.start_recording(output, format='mjpeg')
    camera.start_recording(stream, splitter_port=2, format='h264', motion_output=MyMotionDetector(camera))
    thread02 = Thread(target=motion_detector_wait)
    thread.start()
    thread02.start()
    try:
        address = ('', 8070)
        server = StreamingServer(address, StreamingHandler)
        if not IS_HTTP:
            server.socket = ssl.wrap_socket(server.socket, certfile='/home/pi/fullcert.pem', server_side=True)
        print('Listening on {}'.format(address))
        server.serve_forever()
    finally:
        thread.join()
        thread02.join()
        camera.stop_recording(splitter_port=1)
        camera.stop_recording(splitter_port=2)
