class InvalidTransition(Exception):
    pass


class TCPConnection:
    def __init__(self):
        self.state = "CLOSED"

    def _require(self, *states):
        if self.state not in states:
            raise InvalidTransition(f"illegal in state {self.state}")

    def listen(self):
        self._require("CLOSED")
        self.state = "LISTEN"

    def connect(self):
        self._require("CLOSED")
        self.state = "SYN_SENT"

    def syn_received(self):
        self._require("LISTEN")
        self.state = "SYN_RECEIVED"

    def syn_ack_received(self):
        self._require("SYN_SENT")
        self.state = "ESTABLISHED"

    def ack_received(self):
        if self.state == "SYN_RECEIVED":
            self.state = "ESTABLISHED"
        elif self.state == "FIN_WAIT":
            self.state = "CLOSED_FINAL"
        else:
            raise InvalidTransition(f"illegal ack_received in state {self.state}")

    def close(self):
        if self.state == "ESTABLISHED":
            self.state = "FIN_WAIT"
        elif self.state == "CLOSE_WAIT":
            self.state = "CLOSED_FINAL"
        else:
            raise InvalidTransition(f"illegal close in state {self.state}")

    def fin_received(self):
        self._require("ESTABLISHED")
        self.state = "CLOSE_WAIT"
