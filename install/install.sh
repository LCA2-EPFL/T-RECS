#! /bin/sh

sudo apt-get update
sudo apt-get -y install python3-tk python3-pip python3-dev python3-gi-cairo python3-scipy
sudo pip3 install -r pip_list.txt
