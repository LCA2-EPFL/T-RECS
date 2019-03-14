#!/usr/bin/python3

# The MIT License (MIT)
#
# Copyright (c) 2018 École Polytechnique Fédérale de Lausanne (EPFL)
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

from argparse import ArgumentParser
from csv import writer
from logging import basicConfig, getLogger, INFO
from socket import socket, AF_INET, SOCK_DGRAM
from sys import stdout, exit
from datetime import datetime
from multiprocessing import Process, Queue
from snippets import load_json_file, dump_json_data, load_api
from threading import Thread
from time import sleep
from timeit import default_timer
from math import sqrt

basicConfig(stream=stdout, level=INFO,
                    format='%(asctime)s:%(name)s:%(levelname)s:%(message)s')
logger = getLogger('resource.load')

BUFFER_LIMIT = 1024


def reply(addr, state, bus_index, msg_format):
    """Reply to the resource agent with the Load's state.

    Parameters
    ----------
        addr : tuple
            Address of the RA.

        state : dict
            State of the Load.

    """
    if msg_format == "cpp" :
        measurement_array= [{'P':0, 'Q':0} for i in range(bus_index+1)]
        measurement_array[bus_index] = {
                    'P': -state['P'],  # Sign convention in COMMELEC agents is opposite.
                    'Q': -state['Q']  # Sign convention in COMMELEC agents is opposite.
                }
        message = {
            "buses": measurement_array
        }
        logger.info("Sending setpoint (P = {}, Q = {}) to RA (CPP executable format)"
                    .format(message["buses"][bus_index]['P'], message["buses"][bus_index]['Q']))
    else :
        message = {
            'P': -state['P'],  # Sign convention in COMMELEC agents is opposite.
            'Q': -state['Q']  # Sign convention in COMMELEC agents is opposite.
        }
        logger.info("Sending setpoint (P = {}, Q = {}) to RA (Labview executable format)"
                    .format(message['P'], message['Q']))
    data = dump_json_data(message)
    sock = socket(AF_INET, SOCK_DGRAM)
    sock.sendto(data, addr)


def send(addr, state, period, bus_index, message_format="labview"):
    """Periodically send the state to the resource agent.

    Parameters
    ----------
        addr : tuple
            Address of the RA.

        state : dict
            State of the Load.

        period : float
            Period with which to send.

    """
    while True:
        start_time = default_timer()
        reply(addr, state, bus_index, message_format)
        elapsed_time = default_timer() - start_time
        if elapsed_time < period:
            sleep(period - elapsed_time)


def generate_log(queue, log_path):
    """Write a log to a CSV file, updated whenever the state is changed.

    Parameters
    ----------
        queue : multiprocessing.Queue
            Queue where the state should be put.

        log_path : path_like
            Relative path to which to write the log.

    """
    with open(log_path, 'w', buffering=1, newline='') as log_file:
        log_writer = writer(log_file)
        log_writer.writerow(['Timestamp', 'P', 'Q'])

        while True:
            state = queue.get()
            log_writer.writerow([datetime.now(), state['P'], state['Q']])


def main():
    # Parse the arguments.
    parser = ArgumentParser(
        description="Load that communicates with its resource agent."
    )
    parser.add_argument('config_path',
                        help="Path to the JSON config file for the Load")
    parser.add_argument('--api_path',
                        help="Path to which the GridAPI will be pickled",
                        default='grid_api.pickle')
    parser.add_argument('--params_path',
                        help="Path to file containing the Load parameters")
    args = parser.parse_args()

    # Load the configuration files.
    params = {}
    config = load_json_file(args.config_path, logger)
    if args.params_path == None:
        params = config
    else:
        params = load_json_file(args.params_path, logger)

    # Load the GridAPI.
    # TODO : uncomment
    #api = load_api(args.api_path)

    # Extract some relevant things out of the configuration.
    bus_index = config['bus_index']
    load_path = config['trace_file_abs_path']

    # "labview" : message format for 'old' labview resource-agent executabée
    # "cpp"     : message format for 'new' cpp executable
    # Try/Except for backwards compatibility : if not specified, fallback to old message format
    try :
        message_format = config["message_format"]
    except KeyError :
        message_format = "labview"

        # MODIFIED :
    #Load_addr = api.grid_ip, config['listen_port']
    # RA_addr = config['RA']['ip'], config['RA']['port']
    Load_addr = "127.0.0.1", config['listen_port']
    RA_addr = config['RA']['ip'], config['RA']['listen_port']

    # Read in the Load parameters.
    sample_period = params['sample_period'] / 1e3
    update_period = params['update_period'] / 1e3

    # MODIFIED :
    POWER_FACTOR= params['power_factor']

    queue = Queue()
    state = {
        'P': 0,
        'Q': 0
    }
    queue.put(state)

    # Communicate with the RA.
    Thread(target=send, args=(RA_addr, state, update_period, bus_index, message_format)).start()

    # Run the log generation.
    log_path = config['log_path']
    Process(target=generate_log, args=(queue, log_path)).start()

    try:
        with open(load_path, 'r') as load_file:
            load = load_file.read().splitlines()
            load = [float(S) for S in load]
    except OSError as e:
        logger.error("Could not open {}: {}"
                     .format(load_path, e))
        return 1

    reference_time = default_timer()

    while True:
        start_time = default_timer()
        num_samples = (default_timer() - reference_time) // sample_period
        num_samples = int(num_samples) % len(load)
        S = load[num_samples] * 1e3
        state['P'] = S * POWER_FACTOR
        state['Q'] = S * sqrt(1 - (POWER_FACTOR * POWER_FACTOR))
        logger.info("Implementing setpoint at index {}, (Pd = {}, Qd = {})"
                    .format(bus_index, state['P'], state['Q']))
        api.implement_setpoint(bus_index, state['P'], state['Q'])
        queue.put(state)
        elapsed_time = default_timer() - start_time
        if elapsed_time < sample_period:
            sleep(sample_period - elapsed_time % sample_period)


if __name__ == '__main__':
    exit(main())
