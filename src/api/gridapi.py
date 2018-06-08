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

from socket import socket, AF_INET, SOCK_DGRAM, timeout
from snippets import load_json_data, dump_json_data

BUFFER_LIMIT = 20000


class GridAPI:
    """Send to and receive messages from the grid module using UDP.

    Attributes
    ----------
        base_quantities : dict (class attribute)
            Dict containing the base quantities as specified by the grid.

        grid_ip : str
            IP address of the grid container.

    Notes
    -----
        The GridAPI *must be* initialized by the grid and the grid module
        before it can be used.  In particular, the grid must inform it of the
        base quantities and the grid module must inform it of its address.  It
        will then be pickled to a binary file so that everyone can use it.

    """
    def __init__(self, grid_module_ip, grid_module_port):
        # self.grid_moudle_ip, self.grid_module_port = \
        #     grid_module_ip, grid_module_port
        self.grid_module_ip = grid_module_ip
        self.grid_module_port = grid_module_port

    def ready(self):
        """Make sure that the GrdiAPI has been initialized.

        Returns
        -------
            is_ready : bool
                Whether the GridAPI has been initialized.

        """
        try:
            self.base_quantities
        except AttributeError:
            return False

        return True

    @property
    def base_quantities(self):
        return self._base_quantities

    @base_quantities.setter
    def base_quantities(self, base_quantities):
        """Inform the GridAPI of the grid's base quantities.

        """
        assert {'S', 'V'} <= base_quantities.keys()
        self._base_quantities = base_quantities

    def get_state(self, timeout_s=None):
        """Communicate with the grid module to retrieve the grid's state.

        If a timeout is specified and is exceeded, the GridAPI will attempt to
        return the previously known state.  If no state was known from history,
        then the timeout exception is simply re-raised.

        Parameters
        ----------
            timeout_s : float (optional, default None)
                Timeout in seconds for the UDP communication.

        Returns
        -------
            state : dict
                State of the grid.

        Raises
        ------
            timeout : socket.timeout
                Operation timed out and no state was known from history.

        """
        sock = socket(AF_INET, SOCK_DGRAM)
        sock.settimeout(timeout_s)
        message = {'type': 'request'}
        data = dump_json_data(message)

        try:
            sock.sendto(data, (self.grid_module_ip, self.grid_module_port))
            reply, _ = sock.recvfrom(BUFFER_LIMIT)
        except timeout as e:
            try:
                # Try to return the previous state in the case of a timeout.
                return self._state
            except AttributeError:
                # If impossible, re-raise the timeout exception.
                raise e

        self._state = load_json_data(reply)
        return self._state

    def implement_setpoint(self, bus_index, P, Q):
        """Implement a new setpoint.

        Parameters
        ----------
            bus_index : int
                Index of the bus to update.

            P : float
                New value for the active (P, in W) power.

            Q : float
                New value for the reactive (Q, in Var) power.

        """

        sock = socket(AF_INET, SOCK_DGRAM)
        message = {
            'type': 'implement_setpoint',
            'bus_index': bus_index,
            'P': P,
            'Q': Q
        }
        data = dump_json_data(message)
        sock.sendto(data, (self.grid_module_ip, self.grid_module_port))
