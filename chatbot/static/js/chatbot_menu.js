(function () {
  'use strict';

  var draggedId = null;
  var draggedPaletteType = null;
  var pendienteInsercion = null;
  var reorderUrl = window.CHATBOT_REORDENAR_URL;
  var crearOpcionUrl = window.CHATBOT_CREAR_OPCION_URL;

  function csrfToken() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }

  function idsDescendientes(nodoId) {
    var nodo = document.querySelector('.menu-nodo[data-id="' + nodoId + '"]');
    if (!nodo) return [];
    var ids = [];
    nodo.querySelectorAll('.menu-nodo').forEach(function (n) { ids.push(n.dataset.id); });
    return ids;
  }

  function marcarNoSoltable(nodoId) {
    // Ojo: NO se incluye nodoId en sí mismo acá. .menu-no-drop pone
    // pointer-events:none, y aplicárselo al propio nodo que se está
    // arrastrando corta el drag nativo del navegador a mitad de camino
    // (queda con la opacidad de "arrastrando" pero no se puede soltar en
    // ningún lado). Solo hace falta bloquear los DESCENDIENTES, para no
    // poder re-parentar un nodo dentro de sí mismo.
    idsDescendientes(nodoId).forEach(function (id) {
      var nodo = document.querySelector('.menu-nodo[data-id="' + id + '"]');
      if (nodo) nodo.classList.add('menu-no-drop');
    });
  }

  function limpiarNoSoltable() {
    document.querySelectorAll('.menu-no-drop').forEach(function (n) { n.classList.remove('menu-no-drop'); });
  }

  // ---- Colapsar / expandir menús: se recuerda qué quedó abierto ----
  var LS_ABIERTOS = 'chatbot_menu_abiertos';
  function menusAbiertos() {
    try { return JSON.parse(localStorage.getItem(LS_ABIERTOS) || '[]'); } catch (e) { return []; }
  }
  function guardarAbiertos(arr) {
    try { localStorage.setItem(LS_ABIERTOS, JSON.stringify(arr)); } catch (e) {}
  }
  function marcarAbierto(id) {
    if (!id) return;
    id = String(id);
    var s = menusAbiertos();
    if (s.indexOf(id) === -1) { s.push(id); guardarAbiertos(s); }
  }
  function actualizarContadores() {
    document.querySelectorAll('.menu-nodo').forEach(function (nodo) {
      var badge = nodo.querySelector(':scope > .menu-card .menu-count');
      if (!badge) return;
      var lista = nodo.querySelector(':scope > .menu-hijos > .menu-lista');
      var n = lista ? lista.querySelectorAll(':scope > .menu-nodo').length : 0;
      badge.textContent = n;
      badge.classList.toggle('d-none', n === 0);
    });
  }
  (function restaurarColapsos() {
    menusAbiertos().forEach(function (id) {
      var caja = document.getElementById('menu-hijos-' + id);
      var btn = document.querySelector('[data-bs-target="#menu-hijos-' + id + '"]');
      if (caja) caja.classList.add('show');
      if (btn) { btn.classList.remove('collapsed'); btn.setAttribute('aria-expanded', 'true'); }
    });
    actualizarContadores();
  })();
  document.addEventListener('shown.bs.collapse', function (e) {
    if (e.target.classList && e.target.classList.contains('menu-hijos')) marcarAbierto(e.target.dataset.nodo);
  });
  document.addEventListener('hidden.bs.collapse', function (e) {
    if (!e.target.classList || !e.target.classList.contains('menu-hijos')) return;
    var id = String(e.target.dataset.nodo);
    guardarAbiertos(menusAbiertos().filter(function (x) { return x !== id; }));
  });
  // Los colapsos se manejan sólo a mano: sólo se puede soltar dentro de un menú
  // que esté abierto (o en el menú principal).
  function menuAbierto(cardSubmenu) {
    var caja = cardSubmenu.closest('.menu-nodo').querySelector(':scope > .menu-hijos');
    return !!(caja && caja.classList.contains('show'));
  }

  function guardarOrden(parentId, ordenIds, onOk) {
    fetch(reorderUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify({ parent_id: parentId || null, orden: ordenIds }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          if (onOk) onOk(); else window.location.reload();
        } else {
          alert(data.error || 'No se pudo mover la opción.');
        }
      })
      .catch(function () { alert('Error de conexión al reordenar.'); });
  }

  function idsDeLista(parentId) {
    var lista = parentId
      ? document.querySelector('.menu-nodo[data-id="' + parentId + '"] > .menu-hijos > .menu-lista')
      : document.querySelector('.menu-lista[data-parent-id=""]');
    return lista
      ? Array.prototype.map.call(lista.querySelectorAll(':scope > .menu-nodo'), function (n) { return n.dataset.id; })
      : [];
  }

  document.addEventListener('dragstart', function (e) {
    var chip = e.target.closest('.paleta-item');
    if (chip) {
      draggedPaletteType = chip.dataset.tipo;
      e.dataTransfer.effectAllowed = 'copy';
      e.dataTransfer.setData('text/plain', 'nuevo:' + draggedPaletteType);
      setTimeout(function () { chip.classList.add('arrastrando'); }, 0);
      return;
    }
    var card = e.target.closest('.menu-card');
    if (!card) return;
    var nodo = card.closest('.menu-nodo');
    draggedId = nodo.dataset.id;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', draggedId);
    marcarNoSoltable(draggedId);
    setTimeout(function () {
      card.classList.add('menu-card-arrastrando');
      var papelera = document.getElementById('chatbot-papelera');
      if (papelera) papelera.classList.add('papelera-activa');
      var menuPrincipal = document.getElementById('chatbot-menu-principal');
      if (menuPrincipal) menuPrincipal.classList.add('menu-principal-activo');
    }, 0);
  });

  document.addEventListener('dragend', function () {
    document.querySelectorAll('.menu-card-arrastrando').forEach(function (c) { c.classList.remove('menu-card-arrastrando'); });
    document.querySelectorAll('.paleta-item.arrastrando').forEach(function (c) { c.classList.remove('arrastrando'); });
    document.querySelectorAll('.menu-drop-indicator-activo, .menu-card-drop-activo').forEach(function (n) {
      n.classList.remove('menu-drop-indicator-activo', 'menu-card-drop-activo');
    });
    var papelera = document.getElementById('chatbot-papelera');
    if (papelera) papelera.classList.remove('papelera-activa', 'papelera-hover');
    var menuPrincipal = document.getElementById('chatbot-menu-principal');
    if (menuPrincipal) menuPrincipal.classList.remove('menu-principal-activo', 'menu-principal-hover');
    limpiarNoSoltable();
    draggedId = null;
    draggedPaletteType = null;
  });

  document.addEventListener('dragover', function (e) {
    if (!draggedId && !draggedPaletteType) return;
    var papelera = e.target.closest('#chatbot-papelera');
    if (papelera && draggedId) {
      e.preventDefault();
      papelera.classList.add('papelera-hover');
      return;
    }
    var menuPrincipal = e.target.closest('#chatbot-menu-principal');
    if (menuPrincipal && draggedId) {
      e.preventDefault();
      menuPrincipal.classList.add('menu-principal-hover');
      return;
    }
    var indicador = e.target.closest('.menu-drop-indicator');
    var cardSubmenu = e.target.closest('.menu-card.tipo-submenu');
    if (indicador && !indicador.closest('.menu-no-drop')) {
      e.preventDefault();
      indicador.classList.add('menu-drop-indicator-activo');
    } else if (cardSubmenu && !cardSubmenu.closest('.menu-no-drop') && menuAbierto(cardSubmenu)) {
      e.preventDefault();
      cardSubmenu.classList.add('menu-card-drop-activo');
    }
  });

  document.addEventListener('dragleave', function (e) {
    var papelera = e.target.closest('#chatbot-papelera');
    if (papelera) papelera.classList.remove('papelera-hover');
    var menuPrincipal = e.target.closest('#chatbot-menu-principal');
    if (menuPrincipal) menuPrincipal.classList.remove('menu-principal-hover');
    var indicador = e.target.closest('.menu-drop-indicator');
    if (indicador) indicador.classList.remove('menu-drop-indicator-activo');
    var cardSubmenu = e.target.closest('.menu-card.tipo-submenu');
    if (cardSubmenu) cardSubmenu.classList.remove('menu-card-drop-activo');
  });

  function eliminarOpcionArrastrada(id) {
    if (!confirm('¿Eliminar esta opción? Si tiene otras opciones adentro, también se eliminan.')) return;
    fetch('/chatbot/opciones/' + id + '/eliminar/', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken() },
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) window.location.reload();
        else alert(data.error || 'No se pudo eliminar.');
      })
      .catch(function () { alert('Error de conexión al eliminar.'); });
  }

  document.addEventListener('drop', function (e) {
    if (!draggedId && !draggedPaletteType) return;
    var papelera = e.target.closest('#chatbot-papelera');
    if (papelera && draggedId) {
      e.preventDefault();
      eliminarOpcionArrastrada(draggedId);
      return;
    }
    var menuPrincipal = e.target.closest('#chatbot-menu-principal');
    if (menuPrincipal && draggedId) {
      e.preventDefault();
      var idsRaiz = idsDeLista(null).filter(function (id) { return id !== draggedId; });
      idsRaiz.push(draggedId);
      guardarOrden(null, idsRaiz);
      return;
    }
    var indicador = e.target.closest('.menu-drop-indicator');
    var cardSubmenu = e.target.closest('.menu-card.tipo-submenu');

    if (indicador && !indicador.closest('.menu-no-drop')) {
      e.preventDefault();
      var parentId = indicador.dataset.parent || null;
      var beforeId = indicador.dataset.before || null;

      if (draggedPaletteType) {
        abrirModalCrear(draggedPaletteType, parentId, beforeId);
        return;
      }

      var lista = indicador.closest('.menu-lista');
      var ids = Array.prototype.map.call(lista.querySelectorAll(':scope > .menu-nodo'), function (n) { return n.dataset.id; });
      ids = ids.filter(function (id) { return id !== draggedId; });
      if (beforeId) {
        var idx = ids.indexOf(beforeId);
        ids.splice(idx === -1 ? ids.length : idx, 0, draggedId);
      } else {
        ids.push(draggedId);
      }
      marcarAbierto(parentId);
      guardarOrden(parentId, ids);
    } else if (cardSubmenu && !cardSubmenu.closest('.menu-no-drop') && menuAbierto(cardSubmenu)) {
      e.preventDefault();
      var destinoId = cardSubmenu.closest('.menu-nodo').dataset.id;

      if (draggedPaletteType) {
        abrirModalCrear(draggedPaletteType, destinoId, null);
        return;
      }

      var hijos = cardSubmenu.closest('.menu-nodo').querySelector(':scope > .menu-hijos > .menu-lista');
      var idsHijos = hijos
        ? Array.prototype.map.call(hijos.querySelectorAll(':scope > .menu-nodo'), function (n) { return n.dataset.id; })
        : [];
      idsHijos = idsHijos.filter(function (id) { return id !== draggedId; });
      idsHijos.push(draggedId);
      marcarAbierto(destinoId);
      guardarOrden(destinoId, idsHijos);
    }
  });

  // ---- Modal de alta/edición/borrado de opciones ----
  var modalEl = document.getElementById('chatbot-modal');
  if (!modalEl) return;
  var modal = new bootstrap.Modal(modalEl);
  var modalContent = modalEl.querySelector('.modal-content');

  function aplicarEstilosCampos() {
    modalContent.querySelectorAll('input:not([type=checkbox]):not([type=radio]), textarea, select')
      .forEach(function (el) { el.classList.add(el.tagName === 'SELECT' ? 'form-select' : 'form-control'); });
  }

  var CAMPOS_POR_TIPO = {
    // Submenú/Volver/Inicio son navegación pura: no llevan texto propio.
    // 'orden' no está en el form: se define arrastrando en el árbol.
    SUBMENU: ['texto', 'slug', 'parent', 'archivo', 'activo'],
    RESPUESTA: ['texto', 'slug', 'parent', 'respuesta_texto', 'archivo', 'activo'],
    DERIVACION: ['texto', 'slug', 'parent', 'mensaje_derivacion', 'archivo', 'activo'],
    VOLVER: ['texto', 'slug', 'parent', 'activo'],
    INICIO: ['texto', 'slug', 'parent', 'activo'],
    // "Terminar" tampoco: el mensaje de despedida es único y se edita en la
    // configuración general del panel (ver nota_terminar abajo).
    TERMINAR: ['texto', 'slug', 'parent', 'nota_terminar', 'activo'],
  };

  function actualizarCamposSegunTipo() {
    var selectTipo = modalContent.querySelector('[data-campo="tipo"] select, [data-campo="tipo"] [name="tipo"]');
    if (!selectTipo) return;
    var visibles = CAMPOS_POR_TIPO[selectTipo.value];
    if (!visibles) return;

    modalContent.querySelectorAll('[data-campo]').forEach(function (campo) {
      var nombre = campo.dataset.campo;
      if (nombre === 'tipo') return;
      campo.classList.toggle('d-none', visibles.indexOf(nombre) === -1);
    });
  }

  function bloquearCampoTipo() {
    // Se oculta (no se deshabilita) para que el valor siga viajando en el submit.
    var campoTipo = modalContent.querySelector('[data-campo="tipo"]');
    if (!campoTipo) return;
    var select = campoTipo.querySelector('select, [name="tipo"]');
    var titulo = modalContent.querySelector('.modal-title');
    if (select && titulo && !titulo.querySelector('.badge')) {
      var textoTipo = select.options[select.selectedIndex] ? select.options[select.selectedIndex].text : '';
      var badge = document.createElement('span');
      badge.className = 'badge bg-secondary ms-2';
      badge.style.fontSize = '.65rem';
      badge.textContent = textoTipo;
      titulo.appendChild(badge);
    }
    campoTipo.classList.add('d-none');
  }

  function cargarModal(url, tipoBloqueado) {
    modalContent.innerHTML = '<div class="modal-body text-center text-muted py-5">' +
      '<div class="spinner-border" role="status"></div></div>';
    modal.show();
    fetch(url)
      .then(function (r) { return r.text(); })
      .then(function (html) {
        modalContent.innerHTML = html;
        aplicarEstilosCampos();
        actualizarCamposSegunTipo();
        if (tipoBloqueado) bloquearCampoTipo();
        if (window.iniciarEditoresWhatsApp) window.iniciarEditoresWhatsApp(modalContent);
      })
      .catch(function () {
        modalContent.innerHTML = '<div class="modal-body text-danger">No se pudo cargar.</div>';
      });
  }

  modalEl.addEventListener('change', function (e) {
    if (e.target.matches('[data-campo="tipo"] select, [data-campo="tipo"] [name="tipo"]')) {
      actualizarCamposSegunTipo();
    }
  });

  function abrirModalCrear(tipo, parentId, beforeId) {
    pendienteInsercion = { parentId: parentId || null, beforeId: beforeId || null };
    var url = crearOpcionUrl + '?tipo=' + encodeURIComponent(tipo) + (parentId ? '&parent=' + encodeURIComponent(parentId) : '');
    cargarModal(url, true);
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.btn-abrir-modal-chatbot');
    if (btn) {
      pendienteInsercion = null;
      cargarModal(btn.dataset.url);
    }
  });

  modalEl.addEventListener('submit', function (e) {
    var form = e.target.closest('.form-modal-chatbot');
    if (!form) return;
    e.preventDefault();
    fetch(form.action, { method: 'POST', body: new FormData(form) })
      .then(function (r) {
        if (r.status === 400) {
          return r.text().then(function (html) {
            modalContent.innerHTML = html;
            aplicarEstilosCampos();
            actualizarCamposSegunTipo();
            if (pendienteInsercion) bloquearCampoTipo();
            if (window.iniciarEditoresWhatsApp) window.iniciarEditoresWhatsApp(modalContent);
          });
        }
        return r.json().then(function (data) {
          if (!data.ok) {
            alert(data.error || 'No se pudo guardar.');
            return;
          }
          if (data.id && pendienteInsercion) {
            var ids = idsDeLista(pendienteInsercion.parentId).filter(function (id) { return id !== String(data.id); });
            if (pendienteInsercion.beforeId) {
              var idx = ids.indexOf(String(pendienteInsercion.beforeId));
              ids.splice(idx === -1 ? ids.length : idx, 0, String(data.id));
            } else {
              ids.push(String(data.id));
            }
            pendienteInsercion = null;
            marcarAbierto(data.parent_id);
            guardarOrden(data.parent_id, ids);
          } else {
            window.location.reload();
          }
        });
      })
      .catch(function () { alert('Error de conexión.'); });
  });
})();
