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

import time


class EV():
    def __init__(self, slot_id, P_min, P_max, energy_capacity,
                 SoE_initial, SoE_target, energy_demand, arrival_time, stay_time,
                 delay_initial, lock_time, ramping_time_max):
        self.slot_id = slot_id
        self.P_min = P_min
        self.P_max = P_max
        self.energy_capacity = energy_capacity
        self.SoE_initial = SoE_initial
        self.SoE_target = SoE_target
        self.energy_demand = energy_demand # in kWH
        self.energy_demand_remaining = self.energy_demand
        self.arrival_time = arrival_time
        self.stay_time = stay_time
        self.departure_time = self.arrival_time + self.stay_time
        self.delay_initial = delay_initial
        self.lock_time = lock_time
        self.ramping_time_max = ramping_time_max

        self.P_requested = None
        self.Q_requested = None
        self.request_time = None
        self.P_implemented = 0
        self.Q_implemented = 0
        self.P_implemented_last = 0
        self.Q_implemented_last = 0
        self.isStopped = False


    def set_requested_setpoint(self, P, Q):
        print ("DEBUG: Current requested setpoints: P = {}, and Q = {}".format(self.P_requested, self.Q_requested))
        self.P_requested = float(P)
        self.Q_requested = float(Q)
        self.request_time = time.time()
        print ("DEBUG: Updated requested setpoints: P = {}, and Q = {}".format(self.P_requested, self.Q_requested))


    def update_energy_demand_remaining(self, period):
        if self.P_implemented == self.P_implemented_last:
            self.energy_demand_remaining -= (self.P_implemented_last / 1000 * period / 3600) # P divided by 1000 because it was in watts. Time was in seconds, so, dividing by 3600 to have it in hours.
        elif self.P_implemented > self.P_implemented_last:
            self.energy_demand_remaining -= (self.P_implemented_last / 1000 * period / 3600) + (1 / 2 * (self.P_implemented - self.P_implemented_last) / 1000 * period / 3600)
        else:
            self.energy_demand_remaining -= (self.P_implemented / 1000 * period / 3600) + (1 / 2 * (self.P_implemented_last - self.P_implemented) / 1000 * period / 3600)

        print ("DEBUG: Remaining energy demand for EV at slot {} is {}".format(self.slot_id, self.energy_demand_remaining))


    def ramping(self, period):
        self.P_implemented_last = self.P_implemented

        P_diff = self.P_requested - self.P_implemented
        if P_diff > 0:
            self.P_implemented = min(self.P_implemented + period * self.P_max / self.ramping_time_max, self.P_requested)
        else:
            self.P_implemented = max(self.P_implemented - period * self.P_max / self.ramping_time_max, self.P_requested)

        print ('DEBUG: Ramping from {} to {}'.format(self.P_implemented_last, self.P_implemented))
