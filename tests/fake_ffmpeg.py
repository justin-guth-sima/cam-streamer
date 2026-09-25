"""Stands in for ffmpeg. Usage: fake_ffmpeg.py MODE [ARG]
progress          emit progress blocks every 0.05 s until SIGINT, then exit 255
progress-stop     emit one block then stop emitting but stay alive (stall)
stderr CODE TEXT  print TEXT to stderr, exit with CODE
stderr-long CODE TEXT  print 200000 'x' then \\r then TEXT to stderr, exit with CODE
exit0             exit 0 immediately
ignore-sigint     emit progress, ignore SIGINT forever
"""
import signal
import sys
import time


def emit(frame):
    sys.stdout.write("frame={}\nfps=29.97\nbitrate=2100.5kbits/s\nprogress=continue\n".format(frame))
    sys.stdout.flush()


def main():
    mode = sys.argv[1]
    if mode == "stderr":
        sys.stderr.write(sys.argv[3] + "\n")
        sys.stderr.flush()
        sys.exit(int(sys.argv[2]))
    if mode == "stderr-long":
        sys.stderr.write("x" * 200000 + "\r" + sys.argv[3] + "\n")
        sys.stderr.flush()
        sys.exit(int(sys.argv[2]))
    if mode == "exit0":
        sys.exit(0)
    if mode == "ignore-sigint":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    else:
        signal.signal(signal.SIGINT, lambda *_: sys.exit(255))
    frame = 0
    while True:
        if mode != "progress-stop" or frame == 0:
            emit(frame)
        frame += 1
        time.sleep(0.05)


if __name__ == "__main__":
    main()
