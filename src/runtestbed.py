#!/usr/bin/env python2

# The MIT License (MIT)
#
# Copyright (c) 2018 Ecole Polytechnique Federale de Lausanne (EPFL)
# Author: Jagdish P. Achara
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Utility functions.
"""

from __future__ import division
from pipes import quote
from mininet.net import Mininet
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.link import TCLink
from mininet.util import ipAdd, ipParse, ipStr, netParse
from mininet.clean import Cleanup
from distutils.dir_util import copy_tree

import sys
from inspect import getabsfile
from json import dump, dumps, load
from os import path, access, makedirs, X_OK, walk, getcwd, chdir, pardir
from errno import EEXIST
from signal import SIGALRM, alarm, signal
from itertools import tee
from argparse import Action, ArgumentParser
from shutil import copy2, rmtree
from logging import basicConfig, INFO, getLogger
from time import sleep


GRID_MODULE_LISTEN_PORT = '12347'

LOCAL_IP = '192.168.0.0'
LOCAL_PREFIX = 16

# Local addresses of the LAN 192.168.0.0/16 with the prefix
# (except network and broadcast addresses)
LOCAL_ADDRS = (
    '{}/{}'.format(
        ipAdd(i + 1, LOCAL_PREFIX, ipParse(LOCAL_IP)), LOCAL_PREFIX)
    for i in range((1 << (32 - LOCAL_PREFIX)) - 2)
)


GLOBAL_IP = '10.0.0.0'
GLOBAL_PREFIX = 8
SUBNET_PREFIX = 24

# Host addresses within each subnet with the subnet prefix.
# There is only one host address (10.x.y.1/24) in each subnet
# where each subnet is of the form 10.x.y.0/24
# and is part of the CIDR block 10.0.0.0/8.
HOST_ADDRS = (
    '{}/{}'.format(
        ipAdd(1 + i * (1 << (32 - SUBNET_PREFIX)),
              GLOBAL_PREFIX, ipParse(GLOBAL_IP)),
        SUBNET_PREFIX
    ) for i in range(1 << (SUBNET_PREFIX - GLOBAL_PREFIX))
)

# Router addresses within each subnet with the subnet prefix.
# There is only one router address (10.x.y.254/24) in each subnet
# where each subnet is of the form 10.x.y.0/24
# and is part of the CIDR block 10.0.0.0/8.
ROUTER_ADDRS = (
    '{}/{}'.format(
        ipAdd(254 + i * (1 << (32 - SUBNET_PREFIX)),
              GLOBAL_PREFIX, ipParse(GLOBAL_IP)),
        SUBNET_PREFIX
    ) for i in range(1 << (SUBNET_PREFIX - GLOBAL_PREFIX))
)


# Logging.
basicConfig(stream=sys.stdout, level=INFO,
                    format='%(asctime)s:%(name)s:%(levelname)s:%(message)s')
LOGGER = getLogger('main')

setLogLevel('info')  # Mininet log level


def timeout_handler():
    """Timeout signal handler.

    """
    raise RuntimeError("Simulation timed out")


# Change the behavior of SIGALRM.
signal(SIGALRM, timeout_handler)


def network(ip):
    """Network an IP address belongs to.

    Parameters
    ----------
        ip : str
            IP address.

    Returns
    -------
        network_ip_with_prefix : str
            IP address of the network with prefix.

    """
    ip, prefix = netParse(ip)
    return "{}/{}".format(
        ipStr(ip & (0xffffffff << (32 - prefix))),
        prefix
    )


# From https://stackoverflow.com/questions/3718657/how-to-properly-determine-current-script-directory-in-python
def scriptdir(follow_symlinks=True):
    """Directory in which the script resides.

    Returns
    -------
        script_dir : str
            Directory of the script.

    """
    if getattr(sys, 'frozen', False):
        path_ = path.abspath(scriptdir)
    else:
        path_ = getabsfile(scriptdir)

    if follow_symlinks:
        path_ = path.realpath(path_)

    return path.dirname(path_)


def ensuredirs(dpath, *dpaths):
    """Ensure that a directory exists.

    Do *not* use `if not os.path.exists(...)` because it may lead to a race
    condition.  Use `os.makedirs` directly and process the exception that it
    rises when the directory already exists.  See EAFP at the Python glossary:
    https://docs.python.org/3/glossary.html#term-eafp.

    Parameters
    ----------
        dpath : path_like
            Path to the directory.

    Raises
    ------
        OSError
           Directory could not be created.

    """
    try:
        makedirs(path.join(dpath, *dpaths))
    except OSError as e:
        if e.errno != EEXIST:
            raise  # Re-raise the exception.


def isexe(fpath):
    """Test if a file is executable.

    Parameters
    ----------
        fpath : path_like
            Path to the file.

    Returns
    ------
        result : bool
            Whether the file is executable.

    """
    return path.exists(fpath) and access(fpath, X_OK)


def load_json_file(json_path):
    """Load a JSON file.

    Parameters
    ----------
        json_path : path_like
            Relative path to the JSON file.

    Returns
    -------
        contents : dict
            Contents of the JSON file.

    """
    with open(json_path, 'r') as json_file:
        contents = load(json_file)

    return contents


def check_execs_and_required_files(hosts):
    """Check if the executables and required files are where they should be.

    Parameters
    ----------
        hosts : dict
            Dictionary containing the paths of executable and required files.

    """
    for host in hosts:
        for exec_ in host['executables']:
            if not isexe(exec_['executable_path']):
                raise ValueError("{} is not executable.".format(
                    exec_['executable_path']))

            if not all(map(path.exists,
                            exec_.get('required_files_paths', []))):
                raise ValueError("Required files for {} not found".format(
                    exec_['executable_path']))


def prepare_run_directory(resource_types, trecs_root_dir):
    """Copies all the executables and required files to the runtime directory.

    Parameters
    ----------
        resource_types : set
            A set of different resource types currently being used

        trecs_root_dir : path_like
            Path of T-RECS root directory

    """
    # Copy the executables and required files in 'run' directory.
    sources = {path.join(trecs_root_dir, 'src', 'model', 'grid'),
                path.join(trecs_root_dir, 'src', 'api'),
                path.join(trecs_root_dir, 'src', 'module'),
                path.join(trecs_root_dir, 'src', 'util'),
                path.join(trecs_root_dir, 'src', 'router')}

    for source in sources:
        for dirpath, _, filenames in walk(source):
            for filename in filenames:
                from_path = path.join(dirpath, filename)
                to_path = path.join(trecs_root_dir, 'run', filename)
                copy2(from_path, to_path)

    # From resource model repositories, copy all the directory contents as they are
    for resource in resource_types:
        copy_tree(path.join(trecs_root_dir, 'src', 'model', 'resource', resource),
                    path.join(trecs_root_dir, 'run'))


def add_grid_host(hosts, grid_config_path, sensor_config_path, trecs_root_dir, output_dir):
    """Adds the grid host to the hosts.

    Parameters
    ----------
        hosts : dictionary
            Current dictionary containing hosts specified by the T-RECS user.

        grid_config_path : path_like
            Path of the configuration file for the grid.

        sensor_config_path : path_like
            Path of the configuration file for the sensor.

        trecs_root_dir : path_like
            Path of T-RECS root directory.

        output_dir : path_like
            Directory to which executables' output will be written.

    Returns
    -------
        agents : dict
            Modified agents dictionary with grid host.

    """
    global LOCAL_ADDRS
    LOCAL_ADDRS, local_addrs_backup = tee(LOCAL_ADDRS)

    # first local ip is for the grid host
    for host in hosts:
        ip, _ = local_addrs_backup.next().split('/')

    grid_module_ip, _ = local_addrs_backup.next().split('/')

    grid_host = {'host_name': 'grid', 'host_type': 'grid', 'executables': []}
    exec_details = {'executable_path': '', 'command_line_arguments': [], 'required_files_paths': []}

    exec_details['executable_path'] = path.join(trecs_root_dir, 'run', 'gridmodule.py')
    exec_details['command_line_arguments'] = [grid_config_path, path.join(output_dir, 'csv'), grid_module_ip, GRID_MODULE_LISTEN_PORT]
    grid_host['executables'].append(exec_details)

    exec_details = {'executable_path': '', 'command_line_arguments': [], 'required_files_paths': []}
    exec_details['executable_path'] = path.join(trecs_root_dir, 'run', 'sensormodule.py')
    exec_details['command_line_arguments'] = [sensor_config_path, path.join(output_dir, 'csv'), path.join(trecs_root_dir, 'run', 'mapping_host_ip.json')]
    grid_host['executables'].append(exec_details)

    hosts.append(grid_host)


def add_resource_models_to_ra_hosts(hosts, type_map, trecs_root_dir):
    """ Add resource models to their corresponding agent hosts in agents dictionary.

    Parameters
    ----------
        hosts : dictionary
            Current dictionary containing hosts specified by the T-RECS user.

        type_map : dictionary
            A dictionary mapping resource names to types.

        trecs_root_dir : path_like
            Path of T-RECS root directory.

    Returns
    -------
        agents : dict
            Modified agents dictionary with grid host.
    """

    for host in hosts:
        if host['host_type'] == 'RA':
            resource_name = host['attached_resource_name']
            resource_type = type_map[resource_name]
            exec_details = {'executable_path': '', 'command_line_arguments': [], 'required_files_paths': []}
            exec_details['executable_path'] = path.join(trecs_root_dir, 'run', '{}.py'.format(resource_type))
            exec_details['command_line_arguments'].append('{}_config.json'.format(resource_name))
            host['executables'].append(exec_details)


def relative_to_absolute_path_conversion(hosts, host_config_path):
    cwd = getcwd()
    chdir(path.dirname(host_config_path))
    for host in hosts:
        executables = []
        for executable in host['executables']:
            new_dict = {}
            new_dict['executable_path'] = path.abspath(executable['executable_path'])
            new_dict['command_line_arguments'] = executable['command_line_arguments']
            new_list = []
            for required_file_path in executable['required_files_paths']:
                new_list.append(path.abspath(required_file_path))
            new_dict['required_files_paths'] = new_list
            executables.append(new_dict)
        host['executables'] = executables
    chdir(cwd)


def run(hosts, host_config_path, sensor_config_path, grid_config_path, output_dir, resource_types, type_map, trecs_root_dir, time_limit=None, _loss=None):
    """Run the agents on a Mininet network.

    Parameters
    ----------
        hosts : iterable
            Iterable containing information about each host.

        host_config_path : path_like
            Path to host config file

        sensor_config_path : path_like
            Path to sensor config file

        grid_config_path : path_like
            Path to grid config file

        output_dir : path_like
            Directory to which executables' output will be written.

        resource_types : set
            Set of resource types used in the current T-RECS run.abs

        type_map : dictionary
            A dictionary mapping resource names to types.

        trecs_root_dir : path_like
            Path of T-RECS root directory.

        time_limit : float (optional, default None)
            Maximum time to run the simulation for (in minutes).

        loss : float (optional, default None)
            Network loss (as a fraction of packets).

    """
    relative_to_absolute_path_conversion(hosts, host_config_path)

    check_execs_and_required_files(hosts)

    prepare_run_directory(resource_types, trecs_root_dir)

    # Add grid container so that we can run the grid model.
    add_grid_host(hosts, grid_config_path, sensor_config_path, trecs_root_dir, output_dir)

    # Add resource models to their corresponding resource agent hosts
    add_resource_models_to_ra_hosts(hosts, type_map, trecs_root_dir)

    # Clean the Mininet remains from last run, if any
    Cleanup.cleanup()

    net = Mininet(link=TCLink, xterms=True,
                  ipBase='{}/{}'.format(GLOBAL_IP, GLOBAL_PREFIX))

    net.addController('c0')

    router = None
    switch = None
    vhosts = []
    linkopts = dict(bw=100, delay='0ms', loss=_loss, use_htb=False)  # bw in Mbps

    LOGGER.info("Create the network")

    mapping_host_ip = {}
    for host, host_addr, router_addr, local_addr in zip(
            hosts, HOST_ADDRS, ROUTER_ADDRS, LOCAL_ADDRS
    ):
        if router is None:
            LOGGER.info("Add router to connect subnets and log traffic")
            router = net.addHost('router', ip=router_addr)

            LOGGER.info("Configure router")
            router.cmd('sysctl net.ipv4.ip_forward=1')

        if switch is None:
            LOGGER.info("Add switch to connect grid and RAs")
            switch = net.addSwitch('s0')

        LOGGER.info("Add host {} at {}".format(host['host_name'], host_addr))

        vhost = net.addHost(host['host_name'], ip=host_addr)

        vhosts.append(vhost)

        LOGGER.info("Add link router <-> {}".format(host['host_name']))

        link = net.addLink(router, vhost, **linkopts)
        router.setIP(router_addr, intf=link.intf1)
        vhost.setIP(host_addr, intf=link.intf2)

        # Add an entry to the router's routing table.
        router_ip = router_addr.split('/')[0]
        router.cmd('ip route add {} via {}'.format(
            network(host_addr), router_ip))

        # Set the router as the host's default gateway.
        vhost.cmd('ip route add default via {}'.format(router_ip))

        # If the host is the grid container or an RA, connect it to the LAN.
        if host['host_type'] in ('RA', 'grid'):
            LOGGER.info("Add link switch <-> {}".format(host['host_name']))

            link = net.addLink(switch, vhost, **linkopts)
            vhost.setIP(local_addr, intf=link.intf2)

        mapping_host_ip[host['host_name']] = host_addr

    with open(path.join(trecs_root_dir, 'run', 'mapping_host_ip.json'), 'w') as mapping_file:
        mapping_file.write(dumps(mapping_host_ip))

    LOGGER.info("Start the network")
    net.start()

    sleep(2)  # sleep for 2 seconds

    LOGGER.info("Run the executables")

    # LOGGER.info("Run Scapy on router")
    # exec_path = os.path.join(trecs_root_dir, 'run', 'router', 'sniff.py')
    # router.cmd('{} > {} 2>&1 &'.format(
    #     quote(exec_path),
    #     quote(os.path.join(
    #         output_dir, 'router', 'sniff.py') + '.out')))

    for host, vhost in zip(reversed(hosts), reversed(vhosts)):
        for exec_ in host['executables']:
            path_, exec_name = path.split(exec_['executable_path'])

            LOGGER.info('Run {} on {}.'.format(exec_name, host['host_name']))

            cmd = './{} {} > {} 2>&1 &'.format(
                quote(exec_name),
                ' '.join(exec_['command_line_arguments']),
                quote(path.join(output_dir, host['host_name'], exec_name) + '.out'))

            vhost.cmd('cd {}'.format(path_))
            vhost.cmd(cmd)

    # Check if we should set a time limit.
    if time_limit is not None:
        alarm(int(60 * time_limit))

    LOGGER.info("Run the CLI")
    try:
        CLI(net)
    except RuntimeError as e:
        LOGGER.warn(str(e))
    finally:
        LOGGER.info("Stop the network")
        net.stop()


def create_repos(hosts, output_dir, trecs_root_dir):
    """Deletes existing repositories and creates new repositories.

    Parameters
    ----------
        hosts: iterable
            Iterable containing the hosts for which repos are to be created for output.

        output_dir: path_like
            Directory to which executables' output will be written.

        trecs_root_dir: path_like
            Directory where T-RECS code is present in the system.

    """
    try:
        rmtree(path.join(trecs_root_dir, 'run'))
    except:
        pass

    try:
        rmtree(output_dir)
    except:
        pass

    ensuredirs(trecs_root_dir, 'run')

    ensuredirs(output_dir, 'router')
    ensuredirs(output_dir, 'grid')
    ensuredirs(output_dir, 'csv')
    for host in hosts:
        ensuredirs(output_dir, host['host_name'])


def load_host_config(config_path):
    """Load the host configuration file.

    Parameters
    ----------
        config_path : path_like
            Path to the configuration file.

    Returns
    -------
        config : dict
            The host configuration.

    """
    return load_json_file(config_path)


def load_resource_config(config_path):
    """Load the resources configuration file.

    Parameters
    ----------
        config_path : path_like
            Path to the configuration file.

    Returns
    -------
        config : dict
            The resources configuration.

    """
    return load_json_file(config_path)


def load_grid_config(config_path):
    """Load the grid configuration file.

    Parameters
    ----------
        config_path : path_like
            Path to the grid configuration file.

    Returns
    -------
        (type_map, bus_map, resource_types) : tuple
            Triple consisting of a dictionary mapping resource names to types,
            a dictionary mapping resource names to bus indices, and
            a set of resources.

    """
    config = load_json_file(config_path)

    resource_types = {resource['resource_type'] for resource in config['resources']}

    type_map = {
        resource['resource_name']: resource['resource_type']
        for resource in config['resources']
    }

    bus_map = {
        resource['resource_name']: resource['bus_index']
        for resource in config['resources']
    }

    return type_map, bus_map, resource_types


def load_network_config(config_path):
    """Load the network configuration file.

    Parameters
    ----------
        config_path : path_like
            Path to the network configuration file.

    Returns
    -------
        config : dict
            The network configuration.

    """
    return load_json_file(config_path)


def create_resource_config_files(host_config, resource_config, type_map, bus_map, trecs_root_dir, output_dir, resource_config_dir, model_listen_port, agent_listen_port):
    """Go through each RA and create the configuration file for the resources they are responsible for.

    Parameters
    ----------
        host_config : dictionary
            Dictionary containing hosts specified by the T-RECS user.

        resource_config : dictionary
            Dictionary containing resource configurations specified by the T-RECS user.

        type_map : dictionary
            A dictionary mapping resource names to types.

        bus_map : dictionary
            A dictionary mapping resource names to buses.

        trecs_root_dir : path_like
            Path of T-RECS root directory.

        output_dir : path_like
            Directory to which executables' output will be written.

        resource_config_dir : path_like
            Directory in which resource config resides.

        model_listen_port: int
            The port where model listens for its corresponding agent.

        agent_listen_port: int
            The port where agent listens for its corresponding model.

    """
    for host in host_config:
        if host['host_type'] != 'RA':
            continue

        resource_name = host['attached_resource_name']

        init_data = {
            'RA': {
                'ip': '127.0.0.1',
                'listen_port': agent_listen_port
            },
            'bus_index': bus_map[resource_name],
            'listen_port': model_listen_port,
            'log_path': path.join(output_dir, 'csv', '{}.csv'.format(resource_name))
        }

        resource = next(resource for resource in resource_config['resources'] if resource['resource_name'] == resource_name)
        for key in resource.keys():
            if key.endswith('_path'):
                cwd = getcwd()
                chdir(resource_config_dir)
                resource[key] = path.abspath(resource[key])
                chdir(cwd)

        final_config = init_data.copy()
        final_config.update(resource)

        config_file_name = '{}_config.json'.format(resource_name)
        with open(
            path.join(trecs_root_dir, 'run', config_file_name), 'w'
        ) as init_file:
            dump(final_config, init_file)


# From https://pymotw.com/2/argparse/ or
# https://stackoverflow.com/questions/8632354/python-argparse-custom-actions-with-additional-arguments-passed
class AbsPathAction(Action):
    """Action to convert `argparse` arguments to absolute paths.

    """
    def __init__(self, option_strings, dest, nargs=None, **kwargs):
        if nargs is not None:
            raise ValueError("`nargs` not allowed")
        super(AbsPathAction, self).__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest,
                path.abspath(path.expanduser(values)))


def main():
    """Main function.
    """
    # Parse the arguments.
    # All paths are converted to *absolute* with respect to the working
    # directory from which `runtestbed.py` was invoked.
    parser = ArgumentParser(
        description="Run the T-RECS testbed."
    )
    parser.add_argument('host_config_path',
                        help="path to the host configuration file (absolute or relative to current directory)",
                        action=AbsPathAction)
    parser.add_argument('grid_config_path',
                        help="path to the grid configuration file (absolute or relative to current directory)",
                        action=AbsPathAction)
    parser.add_argument('resource_config_path',
                        help="path to the resource configuration file (absolute or relative to current directory)",
                        action=AbsPathAction)
    parser.add_argument('sensor_config_path',
                        help="path to the sensor configuration file (absolute or relative to current directory)",
                        action=AbsPathAction)
    parser.add_argument('network_config_path',
                        help="path to the network configuration file (absolute or relative to current directory)",
                        action=AbsPathAction)
    parser.add_argument('--output_path',
                        default=path.abspath('output'),
                        help="path of directory to which executables' output will be written (absolute or relative to current directory)",
                        action=AbsPathAction)
    parser.add_argument('--time_limit',
                        help="the time after which the testbed should stop automatically",
                        type=float)
    parser.add_argument('--model_listen_port',
                        default=34343,
                        help="the port on localhost where resource model listens for messages from the resource agent (the resource model is run on the same machine as resource agent)",
                        type=int)
    parser.add_argument('--agent_listen_port',
                        default=43434,
                        help="the port on localhost where resource agent listens for messages from the resource model (the resource model is run on the same machine as resource agent)",
                        type=int)
    args = parser.parse_args()

    # Get T-RECS root directory path (we will need it all along the way)
    trecs_root_dir = path.abspath(path.join(scriptdir(), pardir))

    host_config = load_host_config(args.host_config_path)

    create_repos(host_config['hosts'], args.output_path, trecs_root_dir)

    # Load the configuration files.
    type_map, bus_map, resource_types = \
        load_grid_config(args.grid_config_path)

    resource_config = \
        load_resource_config(args.resource_config_path)

    create_resource_config_files(host_config['hosts'],
                                    resource_config,
                                    type_map,
                                    bus_map,
                                    trecs_root_dir,
                                    args.output_path,
                                    path.dirname(args.resource_config_path),
                                    args.model_listen_port,
                                    args.agent_listen_port)

    network_config = \
        load_network_config(args.network_config_path)

    run(host_config['hosts'],
        args.host_config_path,
        args.sensor_config_path,
        args.grid_config_path,
        args.output_path,
        resource_types,
        type_map,
        trecs_root_dir,
        args.time_limit,
        network_config['loss'])

    return 0


if __name__ == '__main__':
    sys.exit(main())
