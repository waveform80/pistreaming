#!/usr/bin/env python

import sys
import io
import os
import shutil
from subprocess import Popen, PIPE
from string import Template
from struct import Struct
from threading import Thread, Lock
from time import sleep, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from wsgiref.simple_server import make_server

import picamera
import pantilthat as hat
from ws4py.websocket import WebSocket
from ws4py.server.wsgirefserver import (
    WSGIServer,
    WebSocketWSGIHandler,
    WebSocketWSGIRequestHandler,
)
from ws4py.server.wsgiutils import WebSocketWSGIApplication

###########################################
# CONFIGURATION
WIDTH = 640
HEIGHT = 480
FRAMERATE = 24
HTTP_PORT = 8082
WS_PORT = 8084
COLOR = u'#444'
BGCOLOR = u'#333'
JSMPEG_MAGIC = b'jsmp'
JSMPEG_HEADER = Struct('>4sHH')
VFLIP = False
HFLIP = False

###########################################


class StreamingHttpHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == '/':
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
            return
        elif url.path == '/do_orient':
            try:
                data = {k: int(v[0]) for k, v in parse_qs(url.query).items()}
            except (IndexError, ValueError) as e:
                self.send_error(400, str(e))
            else:
                with self.server.hat_lock:
                    hat.servo_enable(1, True)
                    hat.servo_enable(2, True)
                    try:
                        delay = 0.1
                        if 'pan' in data:
                            pan = -data['pan']
                            delay = max(delay, 0.5 * (abs(pan - self.server.last_pan) / 180))
                            hat.pan(pan)
                            self.server.last_pan = pan
                        if 'tilt' in data:
                            tilt = -data['tilt']
                            delay = max(delay, 0.5 * (abs(tilt - self.server.last_tilt) / 180))
                            hat.tilt(tilt)
                            self.server.last_tilt = tilt
                        # Wait for the servo to complete its sweep
                        sleep(delay)
                    finally:
                        hat.servo_enable(1, False)
                        hat.servo_enable(2, False)
                self.send_response(200)
                self.end_headers()
            return
        elif url.path == '/do_light':
            try:
                data = {
                    int(k): (int(r), int(g), int(b), int(w))
                    for k, v in parse_qs(url.query).items()
                    for r, g, b, w in (v[0].split(',', 3),)
                    }
            except (IndexError, ValueError) as e:
                self.send_error(400, str(e))
            else:
                if -1 in data:
                    r, g, b, w = data.pop(-1)
                    for i in range(8):
                        data[i] = r, g, b, w
                with self.server.hat_lock:
                    for index, (r, g, b, w) in data.items():
                        if 0 <= index < 8:
                            hat.set_pixel_rgbw(index, r, g, b, w)
                    hat.show()
                self.send_response(200)
                self.end_headers()
            return
        elif url.path == '/jsmpg.js':
            content_type = 'application/javascript'
            content = self.server.jsmpg_content
        elif url.path == '/index.html':
            content_type = 'text/html; charset=utf-8'
            tpl = Template(self.server.index_template)
            content = tpl.safe_substitute(dict(
                WS_PORT=WS_PORT, WIDTH=WIDTH, HEIGHT=HEIGHT, COLOR=COLOR,
                BGCOLOR=BGCOLOR))
        elif url.path == '/styles.css':
            content_type = 'text/css; charset=utf-8'
            tpl = Template(self.server.styles_template)
            content = tpl.safe_substitute(dict(
                WS_PORT=WS_PORT, WIDTH=WIDTH, HEIGHT=HEIGHT, COLOR=COLOR,
                BGCOLOR=BGCOLOR))
        else:
            self.send_error(404, 'File not found')
            return
        content = content.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', len(content))
        self.send_header('Last-Modified', self.date_time_string(time()))
        self.end_headers()
        if self.command == 'GET':
            self.wfile.write(content)


class StreamingHttpServer(HTTPServer):
    def __init__(self):
        super(StreamingHttpServer, self).__init__(
                ('', HTTP_PORT), StreamingHttpHandler)
        self.hat_lock = Lock()
        self.last_pan = 0
        self.last_tilt = 0
        with io.open('index.html', 'r') as f:
            self.index_template = f.read()
        with io.open('styles.css', 'r') as f:
            self.styles_template = f.read()
        with io.open('jsmpg.js', 'r') as f:
            self.jsmpg_content = f.read()


class StreamingWebSocket(WebSocket):
    def opened(self):
        self.send(JSMPEG_HEADER.pack(JSMPEG_MAGIC, WIDTH, HEIGHT), binary=True)


class BroadcastOutput(object):
    def __init__(self, camera):
        print('Spawning background conversion process')
        self.converter = Popen([
            'ffmpeg',
            '-f', 'rawvideo',
            '-pix_fmt', 'yuv420p',
            '-s', '%dx%d' % camera.resolution,
            '-r', str(float(camera.framerate)),
            '-i', '-',
            '-f', 'mpeg1video',
            '-b', '800k',
            '-r', str(float(camera.framerate)),
            '-'],
            stdin=PIPE, stdout=PIPE, stderr=io.open(os.devnull, 'wb'),
            shell=False, close_fds=True)

    def write(self, b):
        self.converter.stdin.write(b)

    def flush(self):
        print('Waiting for background conversion process to exit')
        self.converter.stdin.close()
        self.converter.wait()


class BroadcastThread(Thread):
    def __init__(self, converter, websocket_server):
        super(BroadcastThread, self).__init__()
        self.converter = converter
        self.websocket_server = websocket_server

    def run(self):
        try:
            while True:
                buf = self.converter.stdout.read1(32768)
                if buf:
                    self.websocket_server.manager.broadcast(buf, binary=True)
                elif self.converter.poll() is not None:
                    break
        finally:
            self.converter.stdout.close()


def main():
    print('Initializing HAT')
    hat.servo_enable(1, False)
    hat.servo_enable(2, False)
    hat.light_mode(hat.WS2812)
    hat.light_type(hat.GRBW)
    print('Initializing camera')
    with picamera.PiCamera() as camera:
        camera.resolution = (WIDTH, HEIGHT)
        camera.framerate = FRAMERATE
        camera.vflip = VFLIP # flips image rightside up, as needed
        camera.hflip = HFLIP # flips image left-right, as needed
        sleep(1) # camera warm-up time
        print('Initializing websockets server on port %d' % WS_PORT)
        WebSocketWSGIHandler.http_version = '1.1'
        websocket_server = make_server(
            '', WS_PORT,
            server_class=WSGIServer,
            handler_class=WebSocketWSGIRequestHandler,
            app=WebSocketWSGIApplication(handler_cls=StreamingWebSocket))
        websocket_server.initialize_websockets_manager()
        websocket_thread = Thread(target=websocket_server.serve_forever)
        print('Initializing HTTP server on port %d' % HTTP_PORT)
        http_server = StreamingHttpServer()
        http_thread = Thread(target=http_server.serve_forever)
        print('Initializing broadcast thread')
        output = BroadcastOutput(camera)
        broadcast_thread = BroadcastThread(output.converter, websocket_server)
        print('Starting recording')
        camera.start_recording(output, 'yuv')
        try:
            print('Starting websockets thread')
            websocket_thread.start()
            print('Starting HTTP server thread')
            http_thread.start()
            print('Starting broadcast thread')
            broadcast_thread.start()
            while True:
                camera.wait_recording(1)
        except KeyboardInterrupt:
            pass
        finally:
            print('Stopping recording')
            camera.stop_recording()
            print('Waiting for broadcast thread to finish')
            broadcast_thread.join()
            print('Shutting down HTTP server')
            http_server.shutdown()
            print('Shutting down websockets server')
            websocket_server.shutdown()
            print('Waiting for HTTP server thread to finish')
            http_thread.join()
            print('Disabling servos')
            hat.servo_enable(1, False)
            hat.servo_enable(2, False)
            hat.clear()
            hat.show()
            print('Waiting for websockets thread to finish')
            websocket_thread.join()


if __name__ == '__main__':
    main()
