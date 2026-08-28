(function () {
  'use strict';

  var EMOJIS = [
    '✅', '❌', '⚠️', 'ℹ️', '❓', '❗',
    '🙏', '👍', '👎', '👋', '💪', '🙌', '👏', '☝️', '👌', '🤝',
    '⏰', '📅', '📍', '📌',
    '💬', '📞', '📱', '✉️', '📧', '🔗', '📎', '📄', '📷',
    '🏢', '🏠', '💼', '💰', '💳', '🛒', '🚗', '✈️', '🏖️',
    '👨‍👩‍👧‍👦', '🧒', '🧓', '🎒', '✏️', '📚',
    '🎉', '🎁', '⭐', '🔥', '❤️', '💙',
  ];

  function escapeHtml(s) {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function renderPreviewWhatsApp(texto) {
    var html = escapeHtml(texto || '');
    // Monospace ```texto```
    html = html.replace(/```([^`\n]+)```/g, '<code>$1</code>');
    // Negrita *texto*
    html = html.replace(/(^|[\s(])\*(\S(?:[^*\n]*\S)?)\*(?=[\s).,!?]|$)/g, '$1<strong>$2</strong>');
    // Itálica _texto_
    html = html.replace(/(^|[\s(])_(\S(?:[^_\n]*\S)?)_(?=[\s).,!?]|$)/g, '$1<em>$2</em>');
    // Tachado ~texto~
    html = html.replace(/(^|[\s(])~(\S(?:[^~\n]*\S)?)~(?=[\s).,!?]|$)/g, '$1<del>$2</del>');
    // Autolink de URLs (WhatsApp las detecta y linkea solo)
    html = html.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
    return html.replace(/\n/g, '<br>');
  }

  function insertar(textarea, antes, despues) {
    var start = textarea.selectionStart;
    var end = textarea.selectionEnd;
    var seleccion = textarea.value.slice(start, end) || 'texto';
    textarea.focus();
    textarea.setRangeText(antes + seleccion + despues, start, end, 'select');
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function insertarListaEnLineas(textarea) {
    var start = textarea.selectionStart;
    var end = textarea.selectionEnd;
    var value = textarea.value;
    var inicioLinea = value.lastIndexOf('\n', start - 1) + 1;
    var finLinea = value.indexOf('\n', end);
    if (finLinea === -1) finLinea = value.length;
    var bloque = value.slice(inicioLinea, finLinea);
    var nuevo = bloque.split('\n').map(function (l) {
      return /^- /.test(l) ? l : '- ' + l;
    }).join('\n');
    textarea.focus();
    textarea.setRangeText(nuevo, inicioLinea, finLinea, 'end');
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function crearEditor(textarea) {
    if (textarea.dataset.waEditorListo) return;
    textarea.dataset.waEditorListo = '1';

    var wrapper = document.createElement('div');
    wrapper.className = 'wa-editor';

    var toolbar = document.createElement('div');
    toolbar.className = 'wa-editor-toolbar';
    toolbar.innerHTML =
      '<button type="button" class="btn btn-outline-secondary btn-sm" data-wa="negrita" title="Negrita"><b>N</b></button>' +
      '<button type="button" class="btn btn-outline-secondary btn-sm" data-wa="italica" title="Itálica"><i>I</i></button>' +
      '<button type="button" class="btn btn-outline-secondary btn-sm" data-wa="tachado" title="Tachado"><s>T</s></button>' +
      '<button type="button" class="btn btn-outline-secondary btn-sm" data-wa="monoespaciado" title="Monoespaciado"><code>&lt;/&gt;</code></button>' +
      '<button type="button" class="btn btn-outline-secondary btn-sm" data-wa="lista" title="Lista"><i class="bi bi-list-ul"></i></button>' +
      '<div class="dropdown d-inline-block">' +
        '<button type="button" class="btn btn-outline-secondary btn-sm" data-bs-toggle="dropdown" title="Emojis">🙂</button>' +
        '<div class="dropdown-menu wa-editor-emojis p-2"></div>' +
      '</div>';

    var hint = document.createElement('div');
    hint.className = 'wa-editor-hint form-text';
    hint.innerHTML = 'Formato de WhatsApp: <code>*negrita*</code>, <code>_itálica_</code>, <code>~tachado~</code>. ' +
      'Los links (http://...) se detectan y muestran como clickeables solos, no hace falta marcarlos.';

    var preview = document.createElement('div');
    preview.className = 'wa-editor-preview';
    preview.innerHTML = '<div class="wa-editor-preview-header"><i class="bi bi-whatsapp"></i> Vista previa</div><div class="wa-editor-preview-bubble"></div>';

    textarea.parentNode.insertBefore(wrapper, textarea);
    wrapper.appendChild(toolbar);
    wrapper.appendChild(textarea);
    wrapper.appendChild(hint);
    wrapper.appendChild(preview);

    var emojiMenu = toolbar.querySelector('.wa-editor-emojis');
    EMOJIS.forEach(function (emoji) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'wa-editor-emoji-btn';
      b.textContent = emoji;
      emojiMenu.appendChild(b);
    });

    // "Tags" para insertar variables ({area}, {nombre}, ...) según data-variables
    var campoWrap = textarea.closest('[data-campo]');
    var variables = (campoWrap && campoWrap.dataset.variables || '')
      .split(',').map(function (v) { return v.trim(); }).filter(Boolean);
    if (variables.length) {
      var sep = document.createElement('span');
      sep.className = 'wa-editor-vars-sep';
      toolbar.appendChild(sep);
      variables.forEach(function (v) {
        var tag = '{' + v + '}';
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn btn-outline-secondary btn-sm wa-editor-var';
        b.textContent = tag;
        b.title = 'Insertar ' + tag;
        b.addEventListener('click', function () {
          textarea.focus();
          textarea.setRangeText(tag, textarea.selectionStart, textarea.selectionEnd, 'end');
          textarea.dispatchEvent(new Event('input', { bubbles: true }));
        });
        toolbar.appendChild(b);
      });
    }

    var bubble = preview.querySelector('.wa-editor-preview-bubble');
    function actualizarPreview() {
      var texto = textarea.value.trim();
      bubble.innerHTML = texto ? renderPreviewWhatsApp(texto) : '<span class="text-muted">(sin texto)</span>';
    }
    actualizarPreview();
    textarea.addEventListener('input', actualizarPreview);

    toolbar.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-wa]');
      if (btn) {
        var accion = btn.dataset.wa;
        if (accion === 'negrita') insertar(textarea, '*', '*');
        else if (accion === 'italica') insertar(textarea, '_', '_');
        else if (accion === 'tachado') insertar(textarea, '~', '~');
        else if (accion === 'monoespaciado') insertar(textarea, '```', '```');
        else if (accion === 'lista') insertarListaEnLineas(textarea);
        return;
      }
      var emojiBtn = e.target.closest('.wa-editor-emoji-btn');
      if (emojiBtn) {
        var start = textarea.selectionStart, end = textarea.selectionEnd;
        textarea.focus();
        textarea.setRangeText(emojiBtn.textContent, start, end, 'end');
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
      }
    });
  }

  window.iniciarEditoresWhatsApp = function (contenedor) {
    contenedor.querySelectorAll(
      '[data-campo="respuesta_texto"] textarea, [data-campo="mensaje_derivacion"] textarea, ' +
      '[data-campo="mensaje_despedida"] textarea, [data-campo="mensaje_bienvenida"] textarea, ' +
      '[data-campo="mensaje_cierre_inactividad"] textarea, ' +
      '[data-campo="plantilla_saludo_inicial"] textarea, [data-campo="plantilla_saludo_area"] textarea'
    ).forEach(crearEditor);
  };
})();
