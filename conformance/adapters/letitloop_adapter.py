from adapters.atomic_wal_adapter import AtomicWalAdapter

class LetItLoopAdapter(AtomicWalAdapter):
    """Backward-compatible adapter alias for AtomicWalAdapter."""
    @property
    def name(self) -> str:
        return "letitloop"
