"""Transition mixin extracted from ui.py without changing behavior."""

from .log import optional_failure

class TransitionMixin:
    """OpenBH-safe reveal. Widgets stay visible if timer callbacks differ by image build."""
    def _transition_init(self, names):
        self._transition_names = list(names)
        self._transition_pos = len(self._transition_names)
        self._transition_timer = None
        self._transition_connection = None
        # Fail-open: never hide essential controls. Some OpenBH builds do not
        # reliably deliver repeated one-shot eTimer callbacks during layout.
        for name in self._transition_names:
            try:
                self[name].show()
            except Exception as exc:
                optional_failure("ui", exc)
    def _transition_step(self):
        if self._transition_pos >= len(self._transition_names):
            return
        name = self._transition_names[self._transition_pos]
        self._transition_pos += 1
        try: self[name].show()
        except Exception as exc: optional_failure("ui",exc)
        if self._transition_pos < len(self._transition_names):
            try: self._transition_timer.start(70, True)
            except Exception as exc: optional_failure("ui",exc)

    def _transition_stop(self):
        try:
            if self._transition_timer is not None:
                self._transition_timer.stop()
        except Exception as exc:
            optional_failure("ui", exc)
        try:
            if self._transition_connection is not None:
                self._transition_connection.disconnect()
        except Exception as exc: optional_failure("ui",exc)
        try:
            if self._transition_step in self._transition_timer.callback:
                self._transition_timer.callback.remove(self._transition_step)
        except Exception as exc: optional_failure("ui",exc)

