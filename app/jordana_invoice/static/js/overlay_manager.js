(function () {
  "use strict";

  var _lockDepth = 0;

  function _lockBody() {
    _lockDepth++;
    if (_lockDepth === 1) {
      document.body.style.overflow = "hidden";
    }
  }

  function _unlockBody() {
    if (_lockDepth > 0) _lockDepth--;
    if (_lockDepth === 0) {
      document.body.style.overflow = "";
    }
  }

  function _safeFocus(el) {
    if (el && document.body.contains(el) && !el.hidden && !el.disabled) {
      try { el.focus(); } catch (_) {}
    }
  }

  function create(config) {
    var overlay = typeof config.overlay === "string"
      ? document.getElementById(config.overlay)
      : config.overlay;
    if (!overlay) return null;

    var closeBtn = config.closeBtn
      ? (typeof config.closeBtn === "string"
        ? document.getElementById(config.closeBtn)
        : config.closeBtn)
      : null;

    var firstFocusSelector = config.firstFocusSelector || "button, input, select, a[href]";
    var keydownHandler = config.keydownHandler || null;
    var cleanupFn = config.cleanupFn || null;
    var bodyLock = config.bodyLock !== false;

    var _returnFocus = null;
    var _pending = false;
    var _disabledButtons = [];
    var _keydownBound = false;
    var _open = false;

    function _onKeydown(e) {
      if (keydownHandler) {
        keydownHandler(e, overlay);
      }
    }

    function open(options) {
      options = options || {};
      if (_open) return;
      _open = true;

      _returnFocus = options.returnFocus || document.activeElement;

      overlay.hidden = false;
      overlay.setAttribute("aria-hidden", "false");

      if (bodyLock) _lockBody();

      if (keydownHandler && !_keydownBound) {
        document.addEventListener("keydown", _onKeydown);
        _keydownBound = true;
      }

      if (closeBtn) {
        closeBtn.onclick = function () { close({}); };
      }

      var self = this;
      requestAnimationFrame(function () {
        if (!_open) return;
        var focusable = overlay.querySelector(firstFocusSelector);
        if (focusable) {
          _safeFocus(focusable);
        }
      });
    }

    function close(options) {
      options = options || {};
      if (!_open) return true;
      _open = false;

      overlay.hidden = true;
      overlay.setAttribute("aria-hidden", "true");

      if (bodyLock) _unlockBody();

      if (_keydownBound) {
        document.removeEventListener("keydown", _onKeydown);
        _keydownBound = false;
      }

      if (cleanupFn) {
        try { cleanupFn(options); } catch (_) {}
      }

      if (_pending) {
        _pending = false;
        _restoreButtons();
      }

      if (options.restoreFocus !== false) {
        _safeFocus(_returnFocus);
      }
      _returnFocus = null;

      return true;
    }

    function beginPending(buttons) {
      if (_pending) return false;
      _pending = true;
      _disabledButtons = [];
      var list = buttons || [];
      for (var i = 0; i < list.length; i++) {
        var btn = typeof list[i] === "string"
          ? document.getElementById(list[i])
          : list[i];
        if (btn && !btn.disabled) {
          btn.disabled = true;
          _disabledButtons.push(btn);
        }
      }
      return true;
    }

    function endPending() {
      _pending = false;
      _restoreButtons();
    }

    function _restoreButtons() {
      for (var i = 0; i < _disabledButtons.length; i++) {
        var btn = _disabledButtons[i];
        if (btn && document.body.contains(btn)) {
          btn.disabled = false;
        }
      }
      _disabledButtons = [];
    }

    function isPending() {
      return _pending;
    }

    function isOpen() {
      return _open;
    }

    function getReturnFocus() {
      return _returnFocus;
    }

    function setReturnFocus(el) {
      _returnFocus = el;
    }

    return {
      open: open,
      close: close,
      beginPending: beginPending,
      endPending: endPending,
      isPending: isPending,
      isOpen: isOpen,
      getReturnFocus: getReturnFocus,
      setReturnFocus: setReturnFocus,
      _overlay: overlay
    };
  }

  window.JordanaOverlay = {
    create: create,
    _lockDepth: function () { return _lockDepth; }
  };
})();
