#!/usr/bin/env python3

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
from csv import DictWriter
from logging import basicConfig, getLogger, INFO
from math import radians, sqrt, cos, sin
from socket import socket, AF_INET, SOCK_DGRAM, timeout
from sys import stdout, exit, exc_info
from os import path
from snippets import load_json_file, load_json_data, dump_json_data, load_api
from threading import Thread
from time import sleep
from timeit import default_timer
from datetime import datetime
from multiprocessing import Process, Queue
from csv import reader, QUOTE_NONNUMERIC

basicConfig(stream=stdout, level=INFO,
                    format='%(asctime)s:%(name)s:%(levelname)s:%(message)s')
logger = getLogger('grid.sensor')

state = None  # State of the grid.
data = None   # Data that will be sent to the GA.


def log_generator(state_queue, log_path):
    """Write logs to CSV files, and update it whenever the state is changed.

    Parameters
    ----------
        state_queue : multiprocessing.Queue
            Queue where the state should be put.

        log_path : path_like
            Relative path to which to write the sensor log bus and freq.


    """

    log_path_bus = path.join(log_path, 'sensor_bus.csv')
    log_path_freq = path.join(log_path, 'sensor_freq.csv')

    log_file_bus = open(log_path_bus, 'w', buffering=1, newline='')

    log_writer_bus = DictWriter(
        log_file_bus, ('Timestamp', 'BusIndex','PhaseIndex', 'P', 'Q', 'Vreal', 'Vimag')
    )
    log_writer_bus.writeheader()

    log_file_freq = open(log_path_freq, 'w', buffering=1, newline='')
    log_writer_freq = DictWriter(
        log_file_freq, ('Timestamp', 'Line #', 'frequency')
    )
    log_writer_freq.writeheader()

    while True:
        # Retrieve the state from the queue.
        state = state_queue.get()

        # curr_time = datetime.now()
        # row = {'Timestamp': curr_time}
        row = {'Timestamp': state['Ts']}

        if 'buses' in state:

            for line in state['buses']:
                row.update({
                    'BusIndex': line['bus_index'],
                    'PhaseIndex': line['phase_index'],
                    'P': line['P'],
                    'Q': line['Q'],
                    'Vreal': line['v_bus_real'],
                    'Vimag': line['v_bus_imag']
                })
                log_writer_bus.writerow(row)

            # row = {'Timestamp': curr_time}
            row = {'Timestamp': state['Ts']}

            row.update({
                'Line #': 0,
                'frequency': state['freq']
            })

            log_writer_freq.writerow(row)

        else:

            log_writer_bus.writerow(row)
            log_writer_freq.writerow(row)

    log_writer_bus.close()
    log_writer_freq.close()


def make_entry(bus_index, phase_index, P, Q, V_real, V_imag):
    """Make a state estimation entry to be sent to the GA.

    Parameters
    ----------
        bus_index : int
            Index of the bus that was sensed.

        phase_index : {1, 2, 3}
            Index of the phase shift that was used.  `1` denotes 0
            degrees, `2` denotes +120 degrees, and `3` denotes -120
            degrees.

        P : float
            Real power (P, in W).

        Q : float
            Reactive power (Q, in Var).

        V_real : float
            Real part of the voltage.

        V_imag : float
            Imaginary part of the voltage.

    Returns
    -------
        entry : dict
            Entry to be sent to the GA.

    """
    assert phase_index in {1, 2, 3}
    return {
        'bus_index': bus_index,
        'phase_index': phase_index,
        'P': P,
        'Q': Q,
        'v_bus_real': V_real,
        'v_bus_imag': V_imag
    }


def update(api, bus_indices, default_line_frequency, period, use_trace, trace_path, state_queue):
    """Update the state of the sensor.

    Parameters
    ----------
        api : GridAPI
            API to use to query the grid for the state.

        bus_indices : iterable
            Which buses to obtain the state for.

        default_line_frequency : float
            default value used for the Frequency of the line.

        period : float
            How often to update the information (in seconds).

        use_trace : boolean
            Define if the frequency is read from a trace or if a static value (default_line_frequency) is used

        trace_path : str
            file path of the frequency trace

        state_queue : multiprocessing.manager.Queue
            Queue in which the updated state will be put (for the log).

    Raises
    ------
        error : IOError
            Could not open the trace

        error : ValueError
            Wrong or missing value in the trace

    """

    # Load trace
    if use_trace:
        try:
            with open(trace_path, 'r') as f:
                reader_ = reader(f, quoting=QUOTE_NONNUMERIC)
                slack_line_frequency = list(reader_)

        except IOError as e:
            logger.error("Could not open {}: {}".format(slack_line_frequency, e))
            return 1
        except ValueError as e:
            logger.error("ValueError, wrong or missing value in {}: {}".format(slack_line_frequency, e))
            return 1
        except Exception as e:
            logger.error("Unexpected error",exc_info()[0])
            raise

        # normalize the timestamp of the trace
        first_freq_ts = slack_line_frequency[0][0]
        for i in range(0,len(slack_line_frequency)):
            slack_line_frequency[i][0] = slack_line_frequency[i][0] - first_freq_ts
        found = False
        end_trace_reach = False
        ptr_ID = -1

    line_frequency = default_line_frequency

    start_time = default_timer()

    global state, data
    bus_indices = frozenset(bus_indices)

    message = {}

    while True:

        try:
            new_state = api.get_state(period)
        except timeout as e:
            logger.warning("Could not retrieve state from GridAPI: {}"
                           .format(e))
            continue

        logger.info("Retrieved state from GridAPI: {}".format(new_state))

        if state != new_state:
            # Update the state.
            state = new_state.copy()

            message = {
                'freq': line_frequency,
                'buses': []
            }

            for bus_index, (P, Q, Vm, Va) in enumerate(
                    zip(state['P'], state['Q'], state['Vm'], state['Va'])
            ):
                if bus_index not in bus_indices:
                    continue
                for phase_index, phase_shift in enumerate((0, 120, -120), 1):
                    phase_angle = radians(Va + phase_shift)
                    message['buses'].append(
                        make_entry(
                            bus_index, phase_index,
                            P / 3, Q / 3,
                            Vm / sqrt(3) * cos(phase_angle),
                            Vm / sqrt(3) * sin(phase_angle)
                        )
                    )

        else:
            logger.info("State remained unchanged")

        if use_trace:
            found = False
            current_time = default_timer() - start_time
            delta = abs(current_time - slack_line_frequency[ptr_ID][0])

            while (not found and not end_trace_reach):

                # reach the end of the trace, take the last element as correct value
                if(ptr_ID + 1) >= len(slack_line_frequency):
                    end_trace_reach = True
                    # take the last entry
                    ptr_ID = -1
                    logger.info('End of trace reached')

                else:

                    next_delta = abs(current_time - slack_line_frequency[ptr_ID + 1][0])

                    # the closest value of current_time is at ptr_ID
                    if next_delta > delta:
                        found = True
                    else:
                        delta = next_delta
                        ptr_ID = ptr_ID + 1

            end_l = default_timer() - start_time
            logger.info('Time spend in nearest algo {}'.format(end_l - current_time))

            line_frequency = slack_line_frequency[ptr_ID][1]

        message['freq'] = line_frequency
        data = dump_json_data(message)

        msg_copy = message.copy()
        msg_copy['Ts'] = datetime.now()
        state_queue.put(msg_copy)

        elapsed_time = default_timer() - start_time
        sleep(period - elapsed_time % period)


def send(sock, addrs):
    """Send data about the grid to the GA.

    Parameters
    ----------
        sock : socket
            Socket to use.  Should be a non-blocking UDP socket.

        addrs : list of tuple
            Address of the GA.

    Raises
    ------
        error : OSError
            Could not send data

    """

    logger.info(addrs)

    if data is None:
        logger.info("No data to send")
    else:
        try:
            for addr in addrs:
                logger.info("Sending data to {}: {}".format(addr, load_json_data(data)))
                sock.sendto(data, addr)
        except OSError as e:
            logger.error("Could not send data: {}"
                         .format(e))
        else:
            logger.info("Data sent.")


def main():
    # Parse the arguments.
    parser = ArgumentParser(
        description="Grid sensor module that communicates with the grid agent."
    )
    parser.add_argument("config_path",
                        help="Path to the JSON config file for the sensor",
                        nargs='?',
                        default='sensor_config.json')
    parser.add_argument("log_path",
                        help="Path to the log directory for the grid",
                        nargs='?')
    parser.add_argument("host_ip_mapping_path",
                        help="Path to the JSON config file for the host ip mapping",
                        nargs='?')
    parser.add_argument("--api_path",
                        help="Path to which the GridAPI will be pickled",
                        default='grid_api.pickle')
    args = parser.parse_args()

    # Load the configuration file.
    config = load_json_file(args.config_path, logger)
    host_ip_mapping = load_json_file(args.host_ip_mapping_path, logger)

    # Read the initialization values.
    sending_freq = config['sensed_info_sending_freq'] / 1e3  # In seconds.
    line_frequency = config['line_frequency']['line_frequency']
    use_trace = config['line_frequency']['use_trace']
    trace_file_path = config['line_frequency']['trace_file_path']

    bus_indices = config['sensed_bus_indices']
    addrs = []
    for receiver in config['receivers_of_sensed_info']:
        addrs.append((host_ip_mapping[receiver['host_name']].split('/')[0], receiver['listen_port']))

    # Load the GridAPI, and make sure it's ready.
    api = load_api(args.api_path)

    state_queue = Queue()

    # Start a thread that will continuously update the data from the grid.
    Thread(target=update,
           args=(api, bus_indices, line_frequency, sending_freq, use_trace, trace_file_path,state_queue)).start()

    log_path = args.log_path
    Process(target=log_generator, args=(state_queue, log_path)).start()

    # Send messages periodically using a non-blocking socket.
    sock = socket(AF_INET, SOCK_DGRAM)
    sock.setblocking(False)
    while True:
        start_time = default_timer()
        send(sock, addrs)
        elapsed_time = default_timer() - start_time
        sleep(sending_freq - elapsed_time % sending_freq)


if __name__ == '__main__':
    exit(main())
