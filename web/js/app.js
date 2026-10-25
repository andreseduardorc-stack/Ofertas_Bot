/**
 * OFERTAS TRACKER — APLICACIÓN WEB
 * Refresco automático cada 1 hora, filtros reactivos y renderizado en cuadrícula.
 */

const REFRESH_INTERVAL_SECONDS = 60 * 60; // 1 hora (3600s)
let segundosRestantes = REFRESH_INTERVAL_SECONDS;
let temporizadorInterval = null;

let todasLasOfertas = [];
let estadisticasGlobales = {};

// Elementos del DOM
const offersGrid = document.getElementById('offersGrid');
const emptyState = document.getElementById('emptyState');
const searchInput = document.getElementById('searchInput');
const btnClearSearch = document.getElementById('btnClearSearch');
const categoryFilter = document.getElementById('categoryFilter');
const storeFilter = document.getElementById('storeFilter');
const ruleFilter = document.getElementById('ruleFilter');
const discountFilter = document.getElementById('discountFilter');
const sortOrder = document.getElementById('sortOrder');
const resultsCount = document.getElementById('resultsCount');
const lastSyncTime = document.getElementById('lastSyncTime');
const countdownEl = document.getElementById('countdown');
const btnRefresh = document.getElementById('btnRefresh');
const btnResetFilters = document.getElementById('btnResetFilters');

// Métricas DOM
const statTotalOfertas = document.getElementById('statTotalOfertas');
const statMaxDescuento = document.getElementById('statMaxDescuento');
const statTiendas = document.getElementById('statTiendas');
const statAlertasTelegram = document.getElementById('statAlertasTelegram');

/**
 * Formatear pesos chilenos ($ CLP)
 */
function formatearCLP(valor) {
  if (valor === null || valor === undefined) return '—';
  return '$' + Math.round(valor).toLocaleString('es-CL');
}

/**
 * Obtener clase CSS de color para la tienda
 */
function obtenerClaseTienda(tienda) {
  const t = (tienda || '').toLowerCase();
  if (t.includes('falabella')) return 'store-falabella';
  if (t.includes('paris')) return 'store-paris';
  if (t.includes('ripley')) return 'store-ripley';
  if (t.includes('lider')) return 'store-lider';
  if (t.includes('pc') && t.includes('factory')) return 'store-pcfactory';
  if (t.includes('sp') || t.includes('digital')) return 'store-spdigital';
  if (t.includes('solotodo')) return 'store-solotodo';
  return 'store-default';
}

/**
 * Imagen SVG de reemplazo elegante cuando no carga la foto del retail
 */
const SVG_FALLBACK = "data:image/svg+xml;charset=UTF-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22200%22%20height%3D%22200%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%2364748b%22%20stroke-width%3D%221.5%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Crect%20x%3D%223%22%20y%3D%223%22%20width%3D%2218%22%20height%3D%2218%22%20rx%3D%222%22%20ry%3D%222%22%3E%3C%2Frect%3E%3Ccircle%20cx%3D%228.5%22%20cy%3D%228.5%22%20r%3D%221.5%22%3E%3C%2Fcircle%3E%3Cpolyline%20points%3D%2221%2015%2016%2010%205%2021%22%3E%3C%2Fpolyline%3E%3C%2Fsvg%3E";

/**
 * Cargar ofertas desde el JSON generado por el tracker
 */
async function cargarOfertas(esManual = false) {
  if (btnRefresh) btnRefresh.classList.add('spinning');
  
  try {
    const respuesta = await fetch(`data/ofertas.json?_t=${Date.now()}`);
    if (!respuesta.ok) {
      throw new Error(`HTTP ${respuesta.status}`);
    }
    const datos = await respuesta.json();
    
    todasLasOfertas = datos.ofertas || [];
    estadisticasGlobales = datos.estadisticas || {};

    // Actualizar métricas
    actualizarMetricas(datos);
    poblarFiltroTiendas(todasLasOfertas);
    renderizar();

    // Actualizar hora de sincronización
    if (datos.actualizado_el) {
      const fecha = new Date(datos.actualizado_el);
      lastSyncTime.textContent = `Última sincronización: ${fecha.toLocaleTimeString('es-CL', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
    }

    if (esManual) {
      mostrarToast('Catálogo sincronizado exitosamente');
    }
  } catch (error) {
    console.error('Error cargando ofertas:', error);
    if (todasLasOfertas.length === 0) {
      resultsCount.textContent = 'Aún no hay ofertas exportadas. Ejecuta python runner.py para generar datos.';
    }
  } finally {
    if (btnRefresh) {
      setTimeout(() => btnRefresh.classList.remove('spinning'), 500);
    }
    // Reiniciar cuenta regresiva a 1 hora
    segundosRestantes = REFRESH_INTERVAL_SECONDS;
  }
}

/**
 * Actualizar panel superior de métricas
 */
function actualizarMetricas(datos) {
  statTotalOfertas.textContent = datos.total_ofertas || todasLasOfertas.length;
  
  const maxDesc = datos.estadisticas?.descuento_maximo || 
    Math.max(0, ...todasLasOfertas.map(o => o.descuento_pct || 0));
  statMaxDescuento.textContent = maxDesc > 0 ? `-${maxDesc}%` : '—';
  
  statTiendas.textContent = datos.estadisticas?.tiendas || 
    new Set(todasLasOfertas.map(o => o.tienda)).size;
    
  statAlertasTelegram.textContent = datos.estadisticas?.alertas_enviadas || 
    todasLasOfertas.filter(o => o.tiene_alerta).length;
}

/**
 * Llenar dinámicamente las tiendas en el desplegable
 */
function poblarFiltroTiendas(ofertas) {
  const seleccionActual = storeFilter.value;
  const tiendas = [...new Set(ofertas.map(o => o.tienda).filter(Boolean))].sort();
  
  storeFilter.innerHTML = '<option value="all">Todas las tiendas</option>';
  tiendas.forEach(t => {
    const opt = document.createElement('option');
    opt.value = t;
    opt.textContent = t.charAt(0).toUpperCase() + t.slice(1);
    storeFilter.appendChild(opt);
  });

  if (tiendas.includes(seleccionActual)) {
    storeFilter.value = seleccionActual;
  }
}

/**
 * Filtrar y ordenar ofertas según los controles
 */
function obtenerOfertasFiltradas() {
  const busqueda = (searchInput.value || '').trim().toLowerCase();
  const tienda = storeFilter.value;
  const regla = ruleFilter.value;
  const minDesc = parseInt(discountFilter.value, 10) || 0;
  const orden = sortOrder.value;

  let filtradas = todasLasOfertas.filter(o => {
    // Búsqueda de texto
    if (busqueda) {
      const matchTitulo = (o.titulo || '').toLowerCase().includes(busqueda);
      const matchTienda = (o.tienda || '').toLowerCase().includes(busqueda);
      if (!matchTitulo && !matchTienda) return false;
    }

    // Filtro categoría
    const categoria = categoryFilter ? categoryFilter.value : 'all';
    if (categoria !== 'all' && (o.categoria || '') !== categoria) {
      return false;
    }

    // Filtro tienda
    if (tienda !== 'all' && o.tienda !== tienda) {
      return false;
    }

    // Filtro regla / alerta
    if (regla === 'con_alerta' && !o.tiene_alerta) return false;
    if (regla !== 'all' && regla !== 'con_alerta' && o.regla !== regla) return false;

    // Filtro descuento mínimo
    if (minDesc > 0 && (o.descuento_pct || 0) < minDesc) {
      return false;
    }

    return true;
  });

  // Ordenamiento
  filtradas.sort((a, b) => {
    if (orden === 'discount_desc') {
      return (b.descuento_pct || 0) - (a.descuento_pct || 0);
    }
    if (orden === 'savings_desc') {
      return (b.ahorro_pesos || 0) - (a.ahorro_pesos || 0);
    }
    if (orden === 'price_asc') {
      return (a.precio_internet || Infinity) - (b.precio_internet || Infinity);
    }
    if (orden === 'recent') {
      const fechaA = new Date(a.fecha_alerta || a.ultima_revision || 0);
      const fechaB = new Date(b.fecha_alerta || b.ultima_revision || 0);
      return fechaB - fechaA;
    }
    return 0;
  });

  return filtradas;
}

/**
 * Renderizar tarjetas en la cuadrícula de 6/4/2 columnas
 */
function renderizar() {
  const ofertas = obtenerOfertasFiltradas();
  resultsCount.textContent = `Mostrando ${ofertas.length} de ${todasLasOfertas.length} ofertas`;

  if (ofertas.length === 0) {
    offersGrid.innerHTML = '';
    emptyState.classList.remove('hidden');
    return;
  }

  emptyState.classList.add('hidden');

  const fragment = document.createDocumentFragment();

  ofertas.forEach(o => {
    const card = document.createElement('article');
    card.className = 'offer-card';

    const claseTienda = obtenerClaseTienda(o.tienda);
    const nombreTienda = o.tienda ? o.tienda.toUpperCase() : 'RETAIL';
    const descPct = o.descuento_pct || 0;
    const tieneAlerta = o.tiene_alerta;

    // Badges
    const badgeDescuento = descPct > 0 
      ? `<span class="discount-badge">-${descPct}%</span>` 
      : '';
    const badgeTienda = `<span class="store-badge ${claseTienda}">${nombreTienda}</span>`;
    
    // Categoría y Regla
    const tagCategoria = o.categoria && o.categoria !== 'Otros'
      ? `<span class="category-tag">${o.categoria}</span>`
      : '';
    const tagRegla = o.regla 
      ? `<span class="rule-tag ${tieneAlerta ? 'is-alerta' : ''}">${tieneAlerta ? '🔔 ' : ''}${o.regla}</span>` 
      : '';

    // Ahorro
    const ahorroHTML = o.ahorro_pesos > 0
      ? `<div class="savings-label">Ahorras ${formatearCLP(o.ahorro_pesos)}</div>`
      : '';

    // Precio referencia (normal tachado)
    const precioRefHTML = o.precio_referencia && o.precio_referencia > o.precio_internet
      ? `<span class="price-normal">${formatearCLP(o.precio_referencia)}</span>`
      : '';

    // Precio tarjeta extra
    const precioTarjetaHTML = o.precio_tarjeta
      ? `<div class="price-card-extra">💳 ${formatearCLP(o.precio_tarjeta)} ${o.tarjeta_asociada ? `(${o.tarjeta_asociada})` : 'con tarjeta'}</div>`
      : '';

    const imgSrc = o.imagen_url && o.imagen_url.trim() ? o.imagen_url : SVG_FALLBACK;

    card.innerHTML = `
      <div class="card-media">
        <div class="card-badges">
          ${badgeDescuento}
          ${badgeTienda}
        </div>
        <img class="card-img" src="${imgSrc}" alt="${o.titulo || 'Producto'}" loading="lazy" onerror="this.onerror=null; this.src='${SVG_FALLBACK}';">
      </div>
      <div class="card-body">
        <div class="card-tags">
          ${tagCategoria}
          ${tagRegla}
        </div>
        <h2 class="product-title" title="${o.titulo}">${o.titulo || 'Sin título'}</h2>
        
        <div class="price-block">
          <div class="main-price-row">
            <span class="price-current">${formatearCLP(o.precio_internet)}</span>
            ${precioRefHTML}
          </div>
          ${ahorroHTML}
          ${precioTarjetaHTML}
        </div>

        <div class="card-actions">
          <a href="${o.url}" target="_blank" rel="noopener noreferrer" class="btn-buy" title="Ver producto en la tienda oficial">
            Ver Oferta ↗
          </a>
          <button class="btn-copy" title="Copiar enlace" data-url="${o.url}">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg>
          </button>
        </div>
      </div>
    `;

    fragment.appendChild(card);
  });

  offersGrid.innerHTML = '';
  offersGrid.appendChild(fragment);
}

/**
 * Temporizador regresivo cada 1 hora
 */
function iniciarTemporizador() {
  if (temporizadorInterval) clearInterval(temporizadorInterval);

  temporizadorInterval = setInterval(() => {
    segundosRestantes--;

    if (segundosRestantes <= 0) {
      cargarOfertas();
      segundosRestantes = REFRESH_INTERVAL_SECONDS;
    }

    const minutos = Math.floor(segundosRestantes / 60);
    const segundos = segundosRestantes % 60;
    countdownEl.textContent = `${minutos.toString().padStart(2, '0')}:${segundos.toString().padStart(2, '0')}`;
  }, 1000);
}

/**
 * Notificación Toast sutil
 */
function mostrarToast(mensaje) {
  const toast = document.createElement('div');
  toast.className = 'toast-notice';
  toast.textContent = mensaje;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2500);
}

// Event Listeners
searchInput.addEventListener('input', () => {
  btnClearSearch.classList.toggle('hidden', !searchInput.value);
  renderizar();
});

btnClearSearch.addEventListener('click', () => {
  searchInput.value = '';
  btnClearSearch.classList.add('hidden');
  renderizar();
});

if (categoryFilter) categoryFilter.addEventListener('change', renderizar);
storeFilter.addEventListener('change', renderizar);
ruleFilter.addEventListener('change', renderizar);
discountFilter.addEventListener('change', renderizar);
sortOrder.addEventListener('change', renderizar);

btnRefresh.addEventListener('click', () => cargarOfertas(true));

btnResetFilters.addEventListener('click', () => {
  searchInput.value = '';
  btnClearSearch.classList.add('hidden');
  if (categoryFilter) categoryFilter.value = 'all';
  storeFilter.value = 'all';
  ruleFilter.value = 'all';
  discountFilter.value = '0';
  sortOrder.value = 'discount_desc';
  renderizar();
});

// Delegación de eventos para copiar enlaces
offersGrid.addEventListener('click', (e) => {
  const btn = e.target.closest('.btn-copy');
  if (btn && btn.dataset.url) {
    navigator.clipboard.writeText(btn.dataset.url)
      .then(() => mostrarToast('¡Enlace copiado al portapapeles!'))
      .catch(() => prompt('Copia este enlace:', btn.dataset.url));
  }
});

// Inicialización
document.addEventListener('DOMContentLoaded', () => {
  cargarOfertas();
  iniciarTemporizador();
});
