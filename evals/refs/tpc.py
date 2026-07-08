class TwoPhaseCommit:
    def __init__(self, participants):
        self.participants = list(participants)
        self.state = 'init'
        self._votes = None
    def prepare(self, votes):
        if self.state != 'init':
            raise RuntimeError('prepare only from init')
        self._votes = dict(votes)
        self.state = 'preparing'
        return 'commit' if all(votes.values()) else 'abort'
    def commit(self):
        if self.state != 'preparing' or not self._votes or not all(self._votes.values()):
            raise RuntimeError('cannot commit')
        self.state = 'committed'
    def abort(self):
        if self.state == 'init':
            self.state = 'aborted'; return
        if self.state == 'preparing' and not all(self._votes.values()):
            self.state = 'aborted'; return
        raise RuntimeError('cannot abort')
