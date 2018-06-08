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
from numpy import interp
from socket import socket, AF_INET, SOCK_DGRAM
from sys import stdout, exit
from datetime import datetime
from multiprocessing import Process, Queue
from threading import Thread
from time import sleep
from timeit import default_timer
from snippets import load_json_file, load_json_data, dump_json_data, load_api
from math import ceil, exp, fabs

basicConfig(stream=stdout, level=INFO,
                    format='%(asctime)s:%(name)s:%(levelname)s:%(message)s')
logger = getLogger('resource.battery')

BUFFER_LIMIT = 1024  # Buffer limit when receiving data.


class Battery():
    def __init__(self, initialU, initialSoC, initialP, initialQ, initialIdc, maxCurrentHourCapacityPerColumnOfCells, Ns, Np, listen_addr, reply_addr):
        self._U = initialU
        self._SoC = initialSoC
        self._P = initialP
        self._Q = initialQ
        self._Idc = initialIdc
        self._maxCurrentHourCapacityPerColumnOfCells = maxCurrentHourCapacityPerColumnOfCells
        self._Ns = Ns
        self._Np = Np
        self._listen_addr = listen_addr
        self._listen_sock = socket(AF_INET, SOCK_DGRAM)
        self._listen_sock.bind(self._listen_addr)
        self._reply_addr = reply_addr
        self._v1 = self._v2 = 0

    def update(self):
        """Update the P and Q of the battery as dictated by its RA.

        """
        while True:
            data, addr = self._listen_sock.recvfrom(BUFFER_LIMIT)
            message = load_json_data(data)
            logger.info("Received message from RA {}: {}".format(addr, message))
            wait_time = abs(self._P - message['Pc']) / self.inverterPowerSlewRate  # TODO verify if only for P? (not Q?)
            logger.info("Now, waiting for {} secs before updating the state of the battery.".format(wait_time))
            sleep(wait_time)
            self._P, self._Q = message['Pc'], message['Qc']

    def _internal_state(self):
        """Internal state of the battery.

        The state is obtained by using linear interpolation through ten points
        corresponding to SoC values of 0, 0.1, 0.2, ... 1.

        """
        return {param: interp(self._SoC, self.measurementSoCpoints, values)
                for param, values in self.LUT.items()}

    def _model(self, I, dt):  # TODO check the computation of this model
        """ TODO complete descritipon of the function

        Parameters
        ----------
            I  : float

            dt : float
                Time difference (in seconds).

        """
        state = self._internal_state()

        tau1 = state['R1'] * state['C1']
        tau2 = state['R2'] * state['C2']

        self._v1 = self._v1 * exp(-dt / tau1) + \
            state['R1'] * (1 - exp(-dt / tau1)) * I
        self._v2 = self._v2 * exp(-dt / tau2) + \
            state['R2'] * (1 - exp(-dt / tau2)) * I

        return state['Em'] - self._v1 - self._v2 - state['R0'] * I

    def implement(self, dt):
        """Implement the P and Q of the battery in terms of its SoC.

        Parameters
        ----------
            dt : float
                Time difference (in seconds).

        """
        dt /= 36e2  # Convert to hours.

        # if initialP < 0, the battery is charging
        Pdc = self._P * self.inverter_efficiency if self._P < 0 else self._P / self.inverter_efficiency

        self._Idc = Pdc / self._U  # TODO check if this computation should be after the computation of new self._U
        self._U = self._model(self._Idc / self._Np, dt)
        self._U *= self._Ns
        self._SoC -= (dt * self._Idc) / self._maxCurrentHourCapacityPerColumnOfCells

    def send(self):
        """Send the state of the battery to its RA.

        """
        sock = socket(AF_INET, SOCK_DGRAM)
        message = {
            'SoC_min': self._SoC,
            'SoC_max': self._SoC,
            'Idc': self._Idc,
            'P': self._P,
            'Q': self._Q
        }
        logger.info("Sending state to RA {}: {}"
                    .format(self._reply_addr, message))
        data = dump_json_data(message)
        sock.sendto(data, self._reply_addr)

    @property
    def SoC(self):
        return self._SoC

    @property
    def P(self):
        return self._P

    @property
    def Q(self):
        return self._Q

    @property
    def state(self):
        return {
            'Ts': datetime.now(),
            'P': self._P,
            'Q': self._Q,
            'SoC': self._SoC,
            'Idc': self._Idc,
            'Udc': self._U
        }


def log_generator(state_queue, log_path):
    """Write a log to a CSV file, updated whenever the state is changed.

    Parameters
    ----------
        state_queue : multiprocessing.Queue
            Queue where the state should be put.

        log_path : path_like
            Relative path to which to write the log.

    """
    with open(log_path, 'w', buffering=1, newline='') as log_file:
        log_writer = writer(log_file)
        log_writer.writerow(['Timestamp', 'P', 'Q', 'SoC', 'Idc', 'Udc'])

        while True:
            state = state_queue.get()
            log_writer.writerow([state['Ts'],
                                 state['P'], state['Q'], state['SoC'],
                                 state['Idc'], state['Udc']])


def main():
    # Parse the arguments.
    parser = ArgumentParser(
        description="Battery that communicates with its resource agent."
    )
    parser.add_argument("config_path",
                        help="Path to the JSON config file for the battery")
    parser.add_argument("--api_path",
                        help="Path to which the GridAPI will be pickled",
                        default='grid_api.pickle')
    args = parser.parse_args()

    # Load the configuration file.
    config = load_json_file(args.config_path, logger)

    # Load the GridAPI.
    api = load_api(args.api_path)

    # Extract configuration information.
    bus_index = config['bus_index']
    listen_addr = '127.0.0.1', config['listen_port']
    reply_addr = config['RA']['ip'], config['RA']['listen_port']
    log_path = config['log_path']

    # refresh period of the battery.
    state_refresh_period = config['refresh_period_for_battery_state'] / 1e3  # Convert to seconds.

    # Read in the battery parameters.
    Battery.LUT = config['cell']['LUT']
    Battery.measurementSoCpoints = Battery.LUT['SoC']
    Battery.inverter_efficiency = config['inverter_efficiency']
    Battery.inverterPowerSlewRate = config['inverterPowerSlewRate']

    initialSoC = config['initialSoC']
    initialP = config['initialP']
    initialQ = config['initialQ']
    inverter_Vmin = config['inverter_Vmin']
    inverter_Vmax = config['inverter_Vmax']
    ratedE = config['ratedE']
    ratedE_cell = config['cell']['ratedE']

    # if initialP < 0, the battery is charging
    initialPdc = initialP * Battery.inverter_efficiency if initialP <= 0 else initialP / Battery.inverter_efficiency

    state = {param: interp(initialSoC, Battery.measurementSoCpoints, values)
                for param, values in Battery.LUT.items()}

    min_Em = min(Battery.LUT['Em'])
    max_Em = max(Battery.LUT['Em'])

    N = ceil(ratedE / ratedE_cell)  # total number of cells
    possibleNs = [i for i in range(1, N + 1) if min_Em * i >= inverter_Vmin and max_Em * i <= inverter_Vmax]  # possible number of cells in series
    possibleNp = [round(N / Ns) for Ns in possibleNs]  # possible number of cells in parallel
    diffs = [(Ns, Np, fabs(N - (Ns * Np))) for Ns, Np in zip(possibleNs, possibleNp)]
    Ns, Np, _ = min(diffs, key=lambda t: t[2])

    # Initial DC voltage is equal to the measured DC voltage at each cell multiplied by the number of cells in series
    initialU = state['Em'] * Ns  # initialU = Em(SoC_initial) * Ns

    # Idc should be computed from initialU  (U at initial SoC)
    initialIdc = initialPdc / initialU

    # maxCurrentHourCapacityPerColumnOfCells = ratedEbattery / ( Em(1) * Ns)
    state = {param: interp(1, Battery.measurementSoCpoints, values)
                for param, values in Battery.LUT.items()}
    maxCurrentHourCapacityPerColumnOfCells = ratedE * 1000 / (state['Em'] * Ns)  # As ratedE is in kWh, to get the Ampere-Hour (not kilo Ampere-Hour) max capacity, we multiply by 1000

    # Initialize the battery.
    battery = Battery(initialU, initialSoC, initialP, initialQ, initialIdc, maxCurrentHourCapacityPerColumnOfCells, Ns, Np, listen_addr, reply_addr)

    # Run the log generator.
    state_queue = Queue()
    state_queue.put(battery.state)
    Process(target=log_generator, args=(state_queue, log_path)).start()

    # Run the RA listener...
    Thread(target=battery.update).start()

    lastImplementedP = lastImplementedQ = 0
    waiting_time = state_refresh_period
    while True:
        start_time = default_timer()

        logger.info("Implementing (P = {}, Q = {})"
                    .format(battery.P, battery.Q))

        if battery.P != lastImplementedP or battery.Q != lastImplementedQ:
            api.implement_setpoint(bus_index, battery.P, battery.Q)
            lastImplementedP = battery.P
            lastImplementedQ = battery.Q

        battery.implement(waiting_time)
        state_queue.put(battery.state)

        battery.send()

        elapsed_time = default_timer() - start_time
        if elapsed_time < state_refresh_period:
            remaining_time = state_refresh_period - elapsed_time
            sleep(remaining_time)
            waiting_time = state_refresh_period
        else:
            waiting_time = elapsed_time


if __name__ == '__main__':
    exit(main())
