# Pi Video Streaming Demo

This is a demonstration for low latency streaming of the Pi's camera module to
any reasonably modern web browser, utilizing Dominic Szablewski's excellent
[JSMPEG project](https://github.com/phoboslab/jsmpeg). Other dependencies are
the Python [ws4py library](http://ws4py.readthedocs.org/), my [picamera
library](http://picamera.readthedocs.org/) (specifically version 1.7 or above),
and [FFmpeg](http://ffmpeg.org).


## Installation

Firstly make sure you've got a functioning Pi camera module (test it with
`raspistill` to be certain). Then make sure you've got the following packages
installed:

    $ sudo apt-get install libav-tools git python3-picamera python3-ws4py python3-pantilthat

Next, clone this repository:

    $ git clone https://github.com/waveform80/pistreaming.git


## Usage

Run the Python server script which should print out a load of stuff
to the console as it starts up:

    $ cd pistreaming
    $ python3 server.py
    Initializing HAT
    Initializing websockets server on port 8084
    Initializing HTTP server on port 8082
    Initializing camera
    Initializing broadcast thread
    Spawning background conversion process
    Starting websockets thread
    Starting HTTP server thread
    Starting broadcast thread

Now fire up your favourite web-browser and visit the address
`http://pi-address:8082/` - it should fairly quickly start displaying the feed
from the camera. You should be able to visit the URL from multiple browsers
simultaneously (although obviously you'll saturate the Pi's bandwidth sooner or
later).

If you find the video stutters or the latency is particularly bad (more than a
second), please check you have a decent network connection between the Pi and
the clients. I've found ethernet works perfectly (even with things like
powerline boxes in between) but a poor wifi connection doesn't provide enough
bandwidth, and dropped packets are not handled terribly well.

To shut down the server press Ctrl+C - you may find it'll take a while
to shut down unless you close the client web browsers (Chrome in particular
tends to keep connections open which will prevent the server from shutting down
until the socket closes).


## Inside the server script

The server script is fairly simple but may look a bit daunting to Python
newbies. There are several major components which are detailed in the following
sections.


### HTTP server

This is implemented in the `StreamingHttpServer` and `StreamingHttpHandler`
classes, and is quite simple:

* In response to an HTTP GET request for "/" it will redirect the client to
  "/index.html".
* In response to an HTTP GET request for "/index.html" it will serve up the
  contents of index.html, replacing @ADDRESS@ with the Pi's IP address and
  the websocket port.
* In response to an HTTP GET request for "/jsmpg.js" it will serve up the
  contents of jsmpg.js verbatim.
* In response to an HTTP GET request for "/do_orient?pan=0&tilt=0" it will
  orient the pan-tilt HAT to the specified values; the sliders present in
  "index.html" are hooked to some JavaScript which will make such requests
* In response to an HTTP GET request for anything else, it will return 404.
* In response to an HTTP HEAD request for any of the above, it will simply
  do the same as for GET but will omit the content.
* In response to any other HTTP method it will return an error.


### Websockets server

This is implemented in the `StreamingWebSocket` class and is ridiculously
simple. In response to a new connection it will immediately send a header
consisting of the four characters "jsmp" and the width and height of the video
stream encoded as 16-bit unsigned integers in big-endian format. This header is
expected by the jsmpg implementation. Other than that, the websocket server
doesn't do much. The actual broadcasting of video data is handled by the
broadcast thread object below.


### Broadcast output

The `BroadcastOutput` class is an implementation of a [picamera custom
output](http://picamera.readthedocs.org/en/latest/recipes2.html#custom-outputs).
On initialization it starts a background FFmpeg process (`avconv`) which is
configured to expect raw video data in YUV420 format, and will encode it as
MPEG1. As unencoded video data is fed to the output via the `write` method, the
class feeds the data to the background FFmpeg process.


### Broadcast thread

The `BroadcastThread` class implements a background thread which continually
reads encoded MPEG1 data from the background FFmpeg process started by the
`BroadcastOutput` class and broadcasts it to all connected websockets. In the
event that no websockets are currently connected the `broadcast` method simply
discards the data. In the event that no more data is available from the FFmpeg
process, the thread checks that the FFmpeg process hasn't finished (with
`poll`) and terminates if it has.


### Main

Finally, the `main` method may look long and complicated but it's mostly
boiler-plate code which constructs all the necessary objects, wraps several of
them in background threads (the HTTP server gets one, the main websockets
server gets another, etc.), configures the camera and starts it recording to
the `BroadcastOutput` object. After that it simply sits around calling
`wait_recording` until someone presses Ctrl+C, at which point it shuts
everything down in an orderly fashion and exits.


## Background

Since authoring the [picamera](http://picamera.readthedocs.org/) library, a
frequent (almost constant!) request has been "how can I stream video to a web
page with little/no latency?" I finally had cause to look into this while
implementing a security camera system using the Pi.

My initial findings were that streaming video over a network is pretty easy:
open a network socket, shove video over it, done! Low latency isn't much of an
issue either; you just need a player that's happy to use a small buffer (e.g.
mplayer). Better still there's plenty of applications which will happily decode
and play the H.264 encoded video streams which the Pi's camera produces ...
unfortunately none of them are web browsers.

When it comes to streaming video to web browsers, the situation at the time of
writing is pretty dire. There's a fair minority of browsers that don't support
H.264 at all. Even those that do have rather variable support for streaming
including weird not-really-standards like Apple's HLS (which usually involves
lots of latency). Then there's the issue that the Pi's camera outputs raw
H.264, and what most browsers want is a nice MPEG transport stream (TS). FFmpeg
seemed like the answer to that, but the version that ships with Raspbian
doesn't seem to like outputting valid PTS (Presentation Time Stamps) with the
Pi's output. Perhaps later versions work better, but I was looking for a
solution that wouldn't involve users having to jump through hoops to create a
custom FFmpeg build (mostly because I could just imagine the amount of extra
support questions I'd get from going that route)!

So, what about other formats? Transcoding to almost anything else (WebM, Ogg,
etc.) is basically out of the question because the Pi's CPU just isn't fast
enough, not to mention none of those really solve the "universal client"
problem as there's plenty of browsers that don't support these formats either.
MJPEG looked an intruiging (if thoroughly backward) possibility but I found it
rather astonishing that we'd have to resort to something as primitive as that.
Surely in this day and age we could at least manage a proper video format?!

Then, out of the blue, and by sheer coincidence a group in Canada got in
contact to ask whether the Pi could produce raw (i.e. unencoded) video output.
This wasn't something I'd ever been asked before but it turned out to be
fairly simple, so I added it to the list of tickets for 1.7 and finished the
code for it about a week later. I confess I pretty much skimmed the rest of
their e-mail the first time I read it, but with the implementation done I went
back and read it properly. They wanted to know whether they could use the
picamera library with Dominic Szablewski's [Javascript-based MPEG1
decoder](http://phoboslab.org/log/2013/09/html5-live-video-streaming-via-websockets).

This was an interesting idea! Javascript implementations are near universal in
browsers these days, and Dominic's decoder was fast enough that it would run
happily even on relatively small platforms (for example it runs on iPhones and
reasonably modern Androids like a Nexus). Furthermore, the Pi is just about
fast enough to handle MPEG1 transcoding with FFmpeg (at least at low
resolutions).

Okay, it's not a modern codec like the excellent H.264. It's not using "proper"
HTML5 video tags. All round, it's still basically a hack, and yes it's pretty
appalling that we have to resort to hacks like this just to come up with a
universally accessible video streaming solution. But hey ... it works, and it's
not (quite) as primitive as MJPEG so I'm happy to declare victory. I spent an
evening bashing together a Python version of the server side. It turned out a
bit too complex to include as a recipe in the docs, hence why it's here, but I
think it provides a reasonable basis for others to work from and extend.

Enjoy!

Dave.
