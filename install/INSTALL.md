# Install Instructions

## Operating System Support
T-RECS is tested and developed on Ubuntu OS.
So for better compatibilty and support, we recommend you to use Ubuntu OS.

## Steps to follow

* Get the T-RECS source by clonning the git repo
  (for this, you will need `git` installed.
  If not installed, install via `sudo apt install git`)  
    - git clone https://c4science.ch/diffusion/5416/t-recs.git
    OR
    - git clone ssh://git@c4science.ch/diffusion/5416/t-recs.git

* Install Mininet from mininet.org/download
  (follow the installation instructions there or below)
    - git clone git://github.com/mininet/mininet
    - as a privileged (root) user, run `./mininet/util/install.sh -a`

* change your current directory to `<trecs_root_dir>/install`

* Run install.sh shell script there.

* Sample agents are statically compiled. For GA and commelecd, you will need libc and other common system libraries that are found in most linux machines.

* For Labview Battery and UCPV RAs, you will need Labview Run-Time Environment (RTE) 2017 (because they are built with Labview RTE 2017) installed.
    * How to install Labview RTE 2017
        - Download the labview runtime environment for Linux [LabVIEW Run-Time_engine](http://www.ni.com/download/labview-run-time-engine-2017-sp1/7194/en/)
        - Convert the rpm package to debian using Alien utility
            - If Alien not installed, `sudo apt install alien`
            - sudo alien --scripts labview-2017-rte-17.0.0-6.x86_64.rpm
            - Install the debian package (sudo dpkg -i labview-2017-rte_17.0.0-4_amd64.deb)
