#!/usr/bin/env python
# -*- coding: utf-8 -*-

from subprocess import call, Popen, PIPE
from spotify import Link, Image, AlbumBrowser, ArtistBrowser
from jukebox import Jukebox, container_loaded
import os
import sys
import argparse
import threading
import time
import re

# Music library imports
import fnmatch
import eyed3
import collections

# playback = False # set if you want to listen to the tracks that are currently ripped (start with "padsp ./jbripper.py ..." if using pulse audio)

pipe = None
ripping = False
end_of_track = threading.Event()
interrupt = threading.Event()

musiclibrary = None
args = None


def printstr(str): # print without newline
    sys.stdout.write(str)
    sys.stdout.flush()


def escape_filename_part(part):
    part = re.sub(r"\s*/\s*", r' & ', part)
    part = re.sub(r"""\s*[\\/:"*?<>|]+\s*""", r' ', part)
    part = part.strip()
    part = re.sub(r"(^\.+\s*|(?<=\.)\.+|\s*\.+$)", r'', part)
    return part


def create_filepath(outputdir, artist, album, title):
    if args.directory is True:
        directory = os.path.join(outputdir, escape_filename_part(artist), escape_filename_part(album))
        mp3file = escape_filename_part(title) + ".mp3"
    else:
        directory = outputdir
        mp3file = escape_filename_part(artist) + " - " + escape_filename_part(title) + " - [ " + escape_filename_part(album) + " ].mp3"

    if not os.path.exists(directory):
        os.makedirs(directory)

    filepath = os.path.join(directory, mp3file)
    return filepath


def rip_init(session, track, outputdir):
    global pipe, ripping
    num_track = "%02d" % (track.index(),)
    artist = artist = ', '.join(a.name() for a in track.artists())
    album = track.album().name()
    title = track.name()

    filepath = create_filepath(outputdir, artist, album, title)

    printstr("ripping " + filepath + " ...\n")
    p = Popen(["lame", "--silent", "-V" + args.vbr, "-h", "-r", "-", filepath], stdin=PIPE)
    pipe = p.stdin
    ripping = True


def rip_terminate(session, track):
    global ripping
    if pipe is not None:
        print(' done!')
        #Avoid concurrent operation exceptions
        if args.playback:
            time.sleep(1)
        pipe.close()
    ripping = False

def rip_delete(track, outputdir):
    artist = artist = ', '.join(a.name() for a in track.artists())
    album = track.album().name()
    title = track.name()
    filepath = create_filepath(outputdir, artist, album, title)
    time.sleep(1)
    print("Deleting partially ripped file at " + filepath)
    call(["rm", "-f", filepath])

def rip(session, frames, frame_size, num_frames, sample_type, sample_rate, channels):
    if ripping:
        printstr('.')
        pipe.write(frames)


def rip_id3(session, track, outputdir): # write ID3 data
    num_track = "%02d" % (track.index(), )
    artist = artist = ', '.join(a.name() for a in track.artists())
    album = track.album().name()
    title = track.name()
    year = track.album().year()

    filepath = create_filepath(outputdir, artist, album, title)

    # remember that we downloaded this song
    musiclibrary[artist][album][title] = filepath

    # download cover
    coverFound = False
    cover = track.album().cover()
    if cover is not None:
        image = session.image_create(cover)
        if image is not None:
            while not image.is_loaded(): # does not work from MainThread!
                time.sleep(0.1)
            fh_cover = open('cover.jpg','wb')
            fh_cover.write(image.data())
            fh_cover.close()
            coverFound = True

    # write ID3 data
    if coverFound:
        if args.oldtags:
            call(["eyeD3", "--to-v2.3", "--add-image", "cover.jpg:FRONT_COVER", "-t", title, "-a", artist, "-A", album, "-n", str(num_track), "-Y", str(year), "-Q", filepath])
        else:
            call(["eyeD3", "--add-image", "cover.jpg:FRONT_COVER", "-t", title, "-a", artist, "-A", album, "-n", str(num_track), "-Y", str(year), "-Q", filepath])
    else:
        if args.oldtags:
            call(["eyeD3", "--to-v2.3", "-t", title, "-a", artist, "-A", album, "-n", str(num_track), "-Y", str(year), "-Q", filepath])
        else:
            call(["eyeD3", "-t", title, "-a", artist, "-A", album, "-n", str(num_track), "-Y", str(year), "-Q", filepath])      
      
    print(filepath + " written")
    # delete cover
    call(["rm", "-f", "cover.jpg"])


def library_scan(path):
    print("Scanning " + path)
    count = 0
    tree = lambda: collections.defaultdict(tree)
    musiclibrary = tree()
    for root, dirnames, filenames in os.walk(path):
        for filename in fnmatch.filter(filenames, '*.mp3'):
            filepath = os.path.join(root, filename)
            try:
                audiofile = eyed3.load(filepath)
                try:
                    artist=audiofile.tag.artist
                except AttributeError:
                    artist=""
                try:
                    album=audiofile.tag.album
                except AttributeError:
                    album=""
                try:
                    title=audiofile.tag.title
                except AttributeError:
                    title=""

                musiclibrary[artist][album][title]=filepath
                count += 1

            except Exception, e:
                print("Error loading " + filepath)
                print(e)
    print(str(count) + " mp3 files found")
    return musiclibrary


def library_track_exists(track, outputdir):
    artist = artist = ', '.join(a.name() for a in track.artists())
    album = track.album().name()
    title = track.name()
    filepathfrominfo = create_filepath(outputdir, artist, album, title)

    return (musiclibrary is not None and musiclibrary[artist][album][title]) or (os.path.exists(filepathfrominfo) and filepathfrominfo)


class RipperThread(threading.Thread):

    def __init__(self, ripper):
        threading.Thread.__init__(self)
        self.ripper = ripper

    def run(self):
        # wait for container
        container_loaded.wait()
        container_loaded.clear()

        # output dir
        outputdir = os.getcwd()
        if args.outputdir != None:
            outputdir = os.path.normpath(os.path.realpath(args.outputdir[0]))

        session = self.ripper.session

        # create track iterator
        link = Link.from_string(args.url[0])
        if link.type() == Link.LINK_TRACK:
            track = link.as_track()
            itrack = iter([track])
        elif link.type() == Link.LINK_PLAYLIST or link.type() == Link.LINK_STARRED:
            playlist = link.as_playlist()
            print('loading playlist ...')
            while not playlist.is_loaded():
                print(' pending playlist ')
                time.sleep(0.5)
            print('done')
            itrack = iter(playlist)
        elif link.type() == Link.LINK_ALBUM:
            album = AlbumBrowser(link.as_album())
            print('loading album ...')            
            while not album.is_loaded():
                print(' pending album ')
                time.sleep(0.5)
            print('done')
            itrack = iter(album)
        elif link.type() == Link.LINK_ARTIST:
            artist = ArtistBrowser(link.as_artist())
            print('loading artist')
            while not artist.is_loaded():
                print(' pending artist ')
                time.sleep(0.5)
            print('done')
            itrack = iter(artist)
                  

        # ripping loop
        count = 0
        for track in itrack:
                count += 1
                # if the track is not loaded, track.availability is not ready
                try:
                    self.ripper.load_track(track)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as inst:
                    if not args.ignoreerrors:
                        raise
                    print("Unexpected error: ", type(inst))
                    print(inst)
                    print("Skipping to next track, if in playlist")
                    continue
                if interrupt.isSet():
                    break
                while not track.is_loaded():
                    time.sleep(0.1)
                if track.availability() != 1:
                    print('Skipping. Track not available')
                else:
                    #self.ripper.load_track(track)
                    exists = library_track_exists(track, outputdir)
                    if exists:
                        print("Skipping. Track found at " + exists)
                    else:
                        try:
                            rip_init(session, track, outputdir)

                            self.ripper.play()

                            end_of_track.wait()
                            end_of_track.clear() # TODO check if necessary

                            rip_terminate(session, track)
                            if interrupt.isSet():
                                rip_delete(track, outputdir)
                                break
                            rip_id3(session, track, outputdir)
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except Exception as inst:
                            if not args.ignoreerrors:
                                raise
                            print("Unexpected error: ", type(inst))
                            print(inst)
                            print("Skipping to next track, if in playlist")

        self.ripper.disconnect()


class Ripper(Jukebox):

    def __init__(self, *a, **kw):
        Jukebox.__init__(self, *a, **kw)
        self.ui = RipperThread(self) # replace JukeboxUI
        self.session.set_preferred_bitrate(1) # 320 kbps

    def music_delivery_safe(self, session, frames, frame_size, num_frames, sample_type, sample_rate, channels):
        rip(session, frames, frame_size, num_frames, sample_type, sample_rate, channels)
        #if playback:
        if args.playback:
            return Jukebox.music_delivery_safe(self, session, frames, frame_size, num_frames, sample_type, sample_rate, channels)
        else:
            return num_frames

    def end_of_track(self, session):
        Jukebox.end_of_track(self, session)
        end_of_track.set()

    def abort_play(self):
        interrupt.set()
        self.stop()
        end_of_track.set()


if __name__ == '__main__':

    parser = argparse.ArgumentParser(prog='jbripper',
        description='Rip Spotify songs',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog='''Example usage:
        rip a single file: ./jbripper.py -u user -p password -U spotify:track:52xaypL0Kjzk0ngwv3oBPR
        rip entire playlist: ./jbripper.py -u user -p password -U spotify:user:username:playlist:4vkGNcsS8lRXj4q945NIA4
        check if file exists before ripping: ./jbripper.py -u user -p password -U spotify:track:52xaypL0Kjzk0ngwv3oBPR -l ~/Music
        ''')
    parser.add_argument('-u', '--user', nargs=1, required=True, help='spotify user')
    parser.add_argument('-p', '--password', nargs=1, required=True, help='spotify password')
    parser.add_argument('-U', '--url', nargs=1, required=True, help='spotify url')
    parser.add_argument('-l', '--library', nargs='?', help='music library path')
    parser.add_argument('-O', '--outputdir', nargs=1, help='music output dir (default is current working directory)')
    parser.add_argument('-P', '--playback', action='store_true', help='set if you want to listen to the tracks that are currently ripped (start with "padsp ./jbripper.py ..." if using pulse audio)')
    parser.add_argument('-V', '--vbr', default='0', help='Lame VBR quality setting. Equivalent to Lame -V parameter. Default 0')
    parser.add_argument('-I', '--ignoreerrors', default=False, action='store_true', help='Ignore encountered errors by skipping to next track in playlist')
    parser.add_argument('-o', '--oldtags', default=False, action='store_true', help='set to write ID3v2 tags version 2.3.0 instead of newer version 2.4.0')
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument('-f', '--file', default=True, action="store_true", help='Save output mp3 file with the following format: "Artist - Song - [ Album ].mp3" (default)')
    group.add_argument('-d', '--directory', default=False, action="store_true", help='Save output mp3 to a directory with the following format: "Artist/Album/Song.mp3"')

    args = parser.parse_args()
    #print args
    if args.library != None:
        musiclibrary = library_scan(args.library)
    else:
        tree = lambda: collections.defaultdict(tree)
        musiclibrary = tree()
    ripper = Ripper(args.user[0], args.password[0]) # login
    try:
        ripper.connect()
    except KeyboardInterrupt:
        print("")
        print("Aborting (KeyboardInterrupt)")
        ripper.abort_play()
