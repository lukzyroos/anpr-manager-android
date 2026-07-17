[app]
title = ANPR Manager
package.name = anprmanager
package.domain = org.anprmanager

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 1.0

requirements = python3,kivy==2.3.0,requests,urllib3,idna,certifi,charset-normalizer

icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png

orientation = portrait
fullscreen = 0

android.permissions = INTERNET
android.api = 33
android.minapi = 21
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1

