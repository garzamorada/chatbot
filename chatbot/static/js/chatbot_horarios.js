/* Lógica compartida de "pintado" por click-y-arrastre para las grillas de
   horario (semanal general y días específicos). Cada celda con clase
   .celda-horario alterna .seleccionada; las que tienen .cerrada no responden. */
(function () {
  'use strict';

  function activarPintadoGrilla(tabla) {
    var pintando = false;
    var modoAgregar = true;

    function toggle(celda, valor) {
      if (celda.classList.contains('cerrada')) return;
      celda.classList.toggle('seleccionada', valor);
    }

    tabla.addEventListener('mousedown', function (e) {
      var celda = e.target.closest('.celda-horario');
      if (!celda || celda.classList.contains('cerrada')) return;
      e.preventDefault();
      pintando = true;
      modoAgregar = !celda.classList.contains('seleccionada');
      toggle(celda, modoAgregar);
    });

    tabla.addEventListener('mouseenter', function (e) {
      var celda = e.target.closest && e.target.closest('.celda-horario');
      if (!celda || !pintando) return;
      toggle(celda, modoAgregar);
    }, true);

    tabla.addEventListener('dragstart', function (e) { e.preventDefault(); });

    document.addEventListener('mouseup', function () { pintando = false; });
  }

  window.activarPintadoGrilla = activarPintadoGrilla;

  window.csrfTokenChatbot = function () {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  };
})();
