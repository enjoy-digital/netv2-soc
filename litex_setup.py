#!/usr/bin/env python3

import os
import sys
from collections import OrderedDict


current_path = os.path.dirname(os.path.realpath(__file__))

# name,  (url, recursive clone, develop)
repos = [
    ("migen", ("http://github.com/m-labs/", True, True)),
    ("litex", ("http://github.com/enjoy-digital/", True, True)),
    ("liteeth", ("http://github.com/enjoy-digital/", False, True)),
    ("litedram", ("http://github.com/enjoy-digital/", False, True)),
    ("litepcie", ("http://github.com/enjoy-digital/", False, True)),
    ("litevideo", ("http://github.com/enjoy-digital/", False, True)),
    ("litescope", ("http://github.com/enjoy-digital/", False, True)),
]
repos = OrderedDict(repos)

if len(sys.argv) < 2:
    print("Available commands:")
    print("- init")
    print("- install")
    print("- update")
    exit()

if sys.argv[1] == "init":
    for name in repos.keys():
        url, need_recursive, need_develop = repos[name]
        # clone repo (recursive if needed)
        print("[cloning " + name + "]...")
        full_url = url + name
        opts = "--recursive" if need_recursive else ""
        os.system("git clone " + full_url + " " + opts)

elif sys.argv[1] == "install":
    for name in repos.keys():
        url, need_recursive, need_develop = repos[name]
        # develop if needed
        print("[installing " + name + "]...")
        if need_develop:
            os.chdir(os.path.join(current_path, name))
            os.system("python3 setup.py develop")

elif sys.argv[1] == "update":
    for name in repos.keys():
        # update
        print("[updating " + name + "]...")
        os.chdir(os.path.join(current_path, name))
        os.system("git pull")