class Rebroadcaster:
    """Background DSU server that mirrors viewer input."""
    def __init__(self):
        self.states = None
        self.stop_event = None
        self.thread = None

    def start(self, port: int):
        """Launch rebroadcast server on the given UDP port."""
        from dsuwu.server import start_server

        if self.stop_event is not None:
            self.stop()
        self.states, self.stop_event, self.thread = start_server(port=port, scripts=[None] * 4)
        return self.states

    def stop(self):
        """Stop the rebroadcast server if running."""
        if self.stop_event is not None:
            self.stop_event.set()
            if self.thread is not None:
                self.thread.join(timeout=0.1)
            self.stop_event = None
            self.thread = None
            self.states = None
