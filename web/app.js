import * as pdfjs from '/vendor/pdfjs/build/pdf.mjs';

pdfjs.GlobalWorkerOptions.workerSrc = '/vendor/pdfjs/build/pdf.worker.mjs';
const $ = id => document.getElementById(id);
const token = location.hash.slice(1) || sessionStorage.getItem('forma-session') || '';
if (location.hash) sessionStorage.setItem('forma-session', token);
history.replaceState(null, '', location.pathname);
const state = { pdf: null, loadingTask: null, name: '', pages: [], mode: 'fill', dirty: false,
  signatures: [], asset: null, selected: null, busy: false, session: null, scale: 1,
  signatureMethod: 'draw', importedImage: null, hasInk: false, fieldCount: 0,
  qrProfile: null, qrImage: null, qrPayload: '', qrError: '', password: '' };
let toastTimer, observer, passwordCallback, passwordReject, qrTimer, qrRequest = 0;

async function api(route, options = {}) {
  const response = await fetch(`/api/${route}`, { ...options, headers: {
    'X-Forma-Token': token, ...options.headers } });
  if (!response.ok) {
    let error;
    try { error = (await response.json()).error; } catch { error = response.statusText; }
    throw new Error(error || 'The operation could not be completed.');
  }
  return response;
}

function notify(message, error = false) {
  clearTimeout(toastTimer);
  $('notification').textContent = message;
  $('notification').classList.toggle('error', error);
  $('notification').hidden = false;
  toastTimer = setTimeout(() => { $('notification').hidden = true; }, error ? 14000 : 10000);
}

function status(message) { $('footer-status').textContent = message; }
function viewportRect(viewport, rect) {
  return [...viewport.convertToViewportPoint(rect[0], rect[1]),
    ...viewport.convertToViewportPoint(rect[2], rect[3])];
}
function dirty(value = true) {
  state.dirty = value;
  $('save-state').hidden = !value;
  $('save-state').textContent = 'Unsaved changes';
}

function updateControls() {
  $('pages').inert = state.busy;
  for (const id of ['fill-tool', 'sign-tool', 'save']) $(id).disabled = state.busy || !state.pdf;
  $('export').disabled = state.busy || !state.pdf || !state.signatures.length;
  $('open').disabled = state.busy;
  for (const id of ['next-field', 'new-signature', 'prev-page', 'next-page', 'zoom-in', 'zoom-out', 'zoom', 'current-page']) $(id).disabled = state.busy;
}

async function work(message, action) {
  if (state.busy) return;
  state.busy = true;
  document.body.classList.add('busy');
  updateControls();
  status(message);
  try { return await action(); }
  catch (error) { console.error(error); notify(error.message, true); status('Could not complete the operation. Your original is untouched.'); }
  finally { state.busy = false; document.body.classList.remove('busy'); updateControls(); }
}

const linkService = {
  eventBus: { dispatch() {} },
  addLinkAttributes(link, url) {
    if (/^(https?:|mailto:)/i.test(url)) {
      link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    }
  },
  getDestinationHash: () => '#', getAnchorUrl: () => '#',
  goToDestination() {}, executeNamedAction(action) {
    if (action === 'NextPage') goToPage(Number($('current-page').value) + 1);
    if (action === 'PrevPage') goToPage(Number($('current-page').value) - 1);
  }, executeSetOCGState() {}, isInPresentationMode: false,
};

function mayReplaceDocument() {
  return !state.dirty || window.confirm('Open another PDF? Unsaved edits and signature placements will be discarded.');
}

async function openBytes(bytes, name) {
  if (!mayReplaceDocument()) return;
  await work('Opening your PDF…', async () => {
    let password = '';
    const task = pdfjs.getDocument({ data: bytes, enableXfa: true,
      cMapUrl: '/vendor/pdfjs/web/cmaps/', cMapPacked: true,
      standardFontDataUrl: '/vendor/pdfjs/web/standard_fonts/',
      wasmUrl: '/vendor/pdfjs/web/wasm/', useSystemFonts: true,
      isEvalSupported: false });
    task.onPassword = (callback, reason) => {
      passwordCallback = value => { password = value; callback(value); };
      passwordReject = () => { task.destroy(); };
      $('password').value = '';
      $('password-error').textContent = reason === pdfjs.PasswordResponses.INCORRECT_PASSWORD ? 'That password did not work. Try again.' : '';
      if (!$('password-dialog').open) $('password-dialog').showModal();
      $('password').focus();
    };
    let pdf;
    try { pdf = await task.promise; }
    catch (error) { await task.destroy(); throw error; }
    if ($('password-dialog').open) $('password-dialog').close();
    if (state.loadingTask) await state.loadingTask.destroy();
    state.loadingTask = task;
    state.pdf = pdf; state.name = name; state.signatures = []; state.asset = null; state.selected = null;
    clearTimeout(qrTimer); ++qrRequest;
    state.qrProfile = null; state.qrImage = null; state.qrPayload = ''; state.qrError = ''; state.password = password;
    state.pages = []; state.fieldCount = 0;
    $('filename').textContent = name;
    document.title = `${name} — Forma`;
    $('welcome').hidden = true; $('pages').hidden = false; $('toolbar').hidden = false;
    $('page-nav').hidden = false;
    $('page-count').textContent = pdf.numPages;
    $('total-pages').textContent = `/ ${pdf.numPages}`;
    $('current-page').max = pdf.numPages;
    $('current-page').value = 1;
    $('page-list').replaceChildren();
    for (let i = 1; i <= pdf.numPages; i++) {
      const button = document.createElement('button');
      const icon = document.createElement('span'); icon.textContent = i;
      button.append(icon, document.createTextNode(`Page ${i}`));
      button.addEventListener('click', () => { if (!state.busy) goToPage(i); });
      $('page-list').append(button);
    }
    const { info } = await pdf.getMetadata();
    if (info.IsXFAPresent && !pdf.isPureXfa) {
      const response = await api('barcodes', { method: 'POST', body: await pdf.getData(),
        headers: { 'Content-Type': 'application/pdf', 'X-Forma-Password': encodeURIComponent(password) } });
      state.qrProfile = (await response.json()).profile;
    }
    $('compatibility').hidden = !info.IsXFAPresent;
    $('compatibility-text').textContent = info.IsXFAPresent
      ? `${state.qrProfile ? 'PPTC 042 QR code supported and updated as you fill. ' : ''}XFA form · General Adobe validation and scripts are not run. Use the toolbar to save; review the form’s submission instructions.` : '';
    if (info.IsXFAPresent && !info.IsAcroFormPresent && !pdf.isPureXfa) {
      $('compatibility-text').textContent = 'This XFA layout is not supported by PDF.js. A fallback page may appear; this document needs another XFA engine.';
    }
    state.pdf.annotationStorage.onSetModified = () => dirty();
    await renderPages();
    $('file-info').textContent = `${pdf.numPages} pages · ${info.IsXFAPresent ? 'XFA form' : 'PDF'} · ${state.fieldCount} fillable fields`;
    setMode('fill'); updateSignatureList(); dirty(false);
    $('main').scrollTop = 0;
    status('Ready to fill. Every save creates a new copy.');
  });
}

async function renderPages() {
  observer?.disconnect();
  $('pages').replaceChildren();
  state.pages = [];
  const first = await state.pdf.getPage(1);
  const fit = Math.max(0.35, Math.min(1.6, ($('main').clientWidth - 64) / first.getViewport({ scale: 1 }).width));
  state.scale = $('zoom').value === 'fit' ? fit : Number($('zoom').value) * 96 / 72;
  const optionalContentConfig = await state.pdf.getOptionalContentConfig();
  state.fieldCount = 0;
  for (let i = 1; i <= state.pdf.numPages; i++) {
    status(`Preparing page ${i} of ${state.pdf.numPages}…`);
    const page = i === 1 ? first : await state.pdf.getPage(i);
    const viewport = page.getViewport({ scale: state.scale });
    const shell = document.createElement('div'); shell.className = 'page-shell'; shell.dataset.page = i;
    shell.setAttribute('aria-label', `Page ${i}`);
    shell.style.width = `${viewport.width}px`; shell.style.height = `${viewport.height}px`;
    shell.style.setProperty('--scale-factor', viewport.scale);
    shell.style.setProperty('--total-scale-factor', viewport.scale * viewport.userUnit);
    const canvas = document.createElement('canvas');
    const outputScale = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.ceil(viewport.width * outputScale); canvas.height = Math.ceil(viewport.height * outputScale);
    canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`;
    shell.append(canvas); $('pages').append(shell);
    const annotationCanvasMap = new Map();
    await page.render({ canvasContext: canvas.getContext('2d'), viewport,
      transform: outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0],
      annotationMode: pdfjs.AnnotationMode.ENABLE_FORMS, annotationCanvasMap,
      optionalContentConfigPromise: Promise.resolve(optionalContentConfig) }).promise;
    const xfaHtml = state.pdf.isPureXfa ? await page.getXfa() : null;
    if (xfaHtml) {
      const layer = document.createElement('div'); shell.append(layer);
      pdfjs.XfaLayer.render({ div: layer, xfaHtml, viewport: viewport.clone({ dontFlip: true }),
        annotationStorage: state.pdf.annotationStorage, linkService });
      state.fieldCount += layer.querySelectorAll('input:not([type=hidden]),textarea,select').length;
    } else {
      const annotations = await page.getAnnotations();
      const layer = document.createElement('div'); layer.className = 'annotationLayer'; shell.append(layer);
      const qrField = state.qrProfile && annotations.find(a => a.fieldName?.endsWith('.PaperFormsBarcode1[0]'));
      const editable = annotations.filter(a => !(a.pushButton || a.isTooltipOnly) && a !== qrField);
      state.fieldCount += editable.filter(a => a.fieldType && !a.readOnly && !a.hidden && a.fieldType !== 'Sig').length;
      const annotationLayer = new pdfjs.AnnotationLayer({ div: layer, page,
        viewport: viewport.clone({ dontFlip: true }), annotationCanvasMap,
        annotationStorage: state.pdf.annotationStorage, linkService });
      await annotationLayer.render({ annotations: editable, renderForms: true,
        enableScripting: false, hasJSActions: false, optionalContentConfig,
        imageResourcesPath: '/vendor/pdfjs/web/images/' });
      for (const annotation of editable) {
        if (!annotation.radioButton) continue;
        const input = layer.querySelector(`input[data-element-id="${CSS.escape(annotation.id)}"]`);
        if (input) input.dataset.qrValue = annotation.buttonValue || '';
      }
      if (qrField) {
        const rect = viewportRect(viewport, qrField.rect);
        const image = document.createElement('img'); image.className = 'qr-preview';
        image.alt = 'Form QR code'; image.hidden = true;
        image.style.left = `${Math.min(rect[0], rect[2])}px`;
        image.style.top = `${Math.min(rect[1], rect[3])}px`;
        image.style.width = `${Math.abs(rect[2] - rect[0])}px`;
        image.style.height = `${Math.abs(rect[3] - rect[1])}px`;
        shell.append(image);
        shell.qrRect = qrField.rect;
      }
    }
    const overlay = document.createElement('div'); overlay.className = 'signature-overlay';
    overlay.addEventListener('pointerdown', event => {
      if (state.busy || state.mode !== 'sign' || event.target !== overlay) return;
      if (!state.asset) { signatureWizard(); return; }
      const box = overlay.getBoundingClientRect();
      placeSignature(i, (event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height);
    });
    shell.append(overlay);
    state.pages.push({ page, shell, viewport, overlay });
    renderSignatures(i);
  }
  observer = new IntersectionObserver(entries => {
    const visible = entries.filter(e => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio);
    if (visible.length) markPage(Number(visible[0].target.dataset.page));
  }, { root: $('main'), threshold: [0.1, 0.3, 0.5, 0.7] });
  state.pages.forEach(p => observer.observe(p.shell));
  await refreshQR();
}

function qrValues() {
  const values = { ...state.qrProfile.values };
  for (const [code, name] of Object.entries(state.qrProfile.bindings)) {
    const controls = [...$('pages').querySelectorAll('[name]')].filter(input => input.name === name);
    if (!controls.length) continue;
    values[code] = controls[0].type === 'radio'
      ? controls.find(input => input.checked)?.dataset.qrValue || '' : controls[0].value;
  }
  return values;
}

async function refreshQR(required = false) {
  clearTimeout(qrTimer);
  if (!state.qrProfile) return;
  const request = ++qrRequest;
  try {
    const result = await (await api('qr', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile: state.qrProfile.id, values: qrValues() }) })).json();
    const image = new Image(); image.src = result.image; await image.decode();
    if (request !== qrRequest) {
      if (required) throw new Error('Form values changed while preparing the QR code. Save again.');
      return;
    }
    state.qrImage = image; state.qrPayload = result.payload; state.qrError = '';
    $('pages').querySelectorAll('.qr-preview').forEach(element => { element.src = result.image; element.hidden = false; });
  } catch (error) {
    if (request !== qrRequest && !required) return;
    state.qrImage = null; state.qrPayload = '';
    $('pages').querySelectorAll('.qr-preview').forEach(element => { element.hidden = true; });
    if (required) throw error;
    if (state.qrError !== error.message) notify(`QR code: ${error.message}`, true);
    state.qrError = error.message;
  }
}

for (const event of ['input', 'change']) $('pages').addEventListener(event, () => {
  if (!state.qrProfile) return;
  ++qrRequest; clearTimeout(qrTimer);
  // A previous QR must never represent newly edited data while an update runs.
  state.qrImage = null; state.qrPayload = '';
  $('pages').querySelectorAll('.qr-preview').forEach(element => { element.hidden = true; });
  qrTimer = setTimeout(() => refreshQR(), 180);
});

function markPage(number) {
  $('current-page').value = number;
  [...$('page-list').children].forEach((button, i) => button.classList.toggle('active', i + 1 === number));
}
function goToPage(number) {
  number = Math.max(1, Math.min(state.pdf?.numPages || 1, Math.round(number) || 1));
  state.pages[number - 1]?.shell.scrollIntoView({ block: 'start', behavior: 'smooth' });
  markPage(number);
}
function setMode(mode) {
  state.mode = mode;
  $('pages').classList.toggle('sign-mode', mode === 'sign');
  $('fill-tool').classList.toggle('active', mode === 'fill');
  $('sign-tool').classList.toggle('active', mode === 'sign');
  $('mode-label').textContent = mode === 'fill' ? '● Fill the highlighted fields' : '● Step 2 · Click the page to place your signature';
  $('next-field').hidden = mode !== 'fill';
  $('new-signature').hidden = mode !== 'sign';
  if (mode === 'fill') { state.selected = null; state.pages.forEach((_, i) => renderSignatures(i + 1)); }
}

function nextField() {
  const fields = [...$('pages').querySelectorAll('input:not([type=hidden]):not([readonly]):not([disabled]),textarea:not([readonly]),select:not([disabled])')]
    .filter(e => e.getBoundingClientRect().width > 0);
  const index = fields.indexOf(document.activeElement);
  const field = fields[(index + 1) % fields.length];
  if (field) { field.scrollIntoView({ block: 'center' }); field.focus({ preventScroll: true }); }
}

async function draftBytes() {
  document.activeElement?.blur();
  await refreshQR(true);
  const data = state.pdf.annotationStorage.size ? await state.pdf.saveDocument() : await state.pdf.getData();
  if (!state.qrProfile) return data;
  const response = await api('qr-pdf', { method: 'POST', body: data,
    headers: { 'Content-Type': 'application/pdf', 'X-Forma-Password': encodeURIComponent(state.password) } });
  return new Uint8Array(await response.arrayBuffer());
}

let saveDialogResolve = null, saveDialogFolder = '', folderRequest = 0;
async function browseSaveFolder(folder) {
  const request = ++folderRequest;
  $('confirm-save').disabled = true;
  $('save-dialog-error').textContent = '';
  try {
    const listing = await (await api('folders', { headers: { 'X-Forma-Folder': encodeURIComponent(folder) } })).json();
    if (request !== folderRequest || !$('save-dialog').open) return;
    saveDialogFolder = listing.folder;
    $('save-folder').value = listing.folder;
    $('save-up').dataset.folder = listing.parent;
    $('save-home').dataset.folder = listing.home;
    $('save-up').disabled = listing.parent === listing.folder;
    $('save-entries').replaceChildren();
    for (const entry of listing.entries) {
      const row = document.createElement(entry.directory ? 'button' : 'div');
      row.className = 'folder-entry';
      const icon = document.createElement('span'); icon.textContent = entry.directory ? '▱' : '▤';
      const name = document.createElement('span'); name.textContent = entry.name;
      row.append(icon, name);
      if (entry.directory) {
        row.type = 'button'; row.addEventListener('click', () => browseSaveFolder(entry.path));
      } else {
        row.classList.add('existing-pdf');
        const label = document.createElement('small'); label.textContent = 'Existing file'; row.append(label);
      }
      $('save-entries').append(row);
    }
    if (!listing.entries.length) {
      const empty = document.createElement('p'); empty.textContent = 'No folders or PDFs here. You can save your new copy in this folder.';
      $('save-entries').append(empty);
    }
    if (listing.truncated) $('save-dialog-error').textContent = 'Showing the first 1,000 entries. Use the folder path to navigate further.';
    $('confirm-save').disabled = false;
  } catch (error) {
    if (request === folderRequest) $('save-dialog-error').textContent = error.message;
  }
}

function finishSaveDialog(path) {
  ++folderRequest;
  $('save-dialog').close();
  const resolve = saveDialogResolve; saveDialogResolve = null;
  resolve?.(path);
}

async function chooseDestination(kind) {
  status('Choose the folder and filename in Save As…');
  const choice = await (await api('choose-save', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind, name: state.name }) })).json();
  if (choice.cancelled) return null;
  if (choice.native) return choice.path;
  $('save-dialog-title').textContent = kind === 'signed' ? 'Export signed PDF' : 'Save editable copy';
  $('save-filename').value = choice.filename;
  $('save-dialog-error').textContent = '';
  $('save-entries').replaceChildren();
  saveDialogFolder = '';
  const result = new Promise(resolve => { saveDialogResolve = resolve; });
  $('save-dialog').showModal();
  browseSaveFolder(choice.folder);
  return result;
}

async function writeChosenPDF(path, data) {
  const response = await api('save', { method: 'POST', body: data,
    headers: { 'Content-Type': 'application/pdf', 'X-Forma-Destination': encodeURIComponent(path) } });
  return (await response.json()).paths[0];
}

async function saveEditable() {
  if (!state.pdf) return;
  await work('Choose where to save your editable copy…', async () => {
    const destination = await chooseDestination('editable');
    if (!destination) { status('Save cancelled. Your edits are still here.'); return; }
    status('Saving your editable copy…');
    const data = await draftBytes();
    const path = await writeChosenPDF(destination, data);
    dirty(state.signatures.length > 0);
    notify(`Editable copy saved\n${path}${state.signatures.length ? '\nSignature placements are included only in signed export.' : ''}`);
    status(`Saved editable copy · ${path}`);
  });
}

async function exportSigned() {
  if (!state.pdf || !state.signatures.length) return;
  await work('Choose where to export your signed PDF…', async () => {
    if (state.pdf.numPages > 100) throw new Error('Signed export supports up to 100 pages. You can still save an editable copy.');
    const destination = await chooseDestination('signed');
    if (!destination) { status('Export cancelled. Your edits and signatures are still here.'); return; }
    document.activeElement?.blur();
    await refreshQR(true);
    const pages = [];
    for (let i = 0; i < state.pages.length; i++) {
      status(`Exporting page ${i + 1} of ${state.pdf.numPages}…`);
      const { page, shell } = state.pages[i];
      const viewport = page.getViewport({ scale: 2 });
      let rendered;
      if (state.pdf.isPureXfa) {
        await document.fonts.ready;
        rendered = await window.html2canvas(shell, { scale: 2 / state.scale,
          backgroundColor: '#ffffff', logging: false,
          ignoreElements: element => element.classList.contains('signature-overlay'),
          onclone: doc => doc.querySelectorAll('input,textarea,select').forEach(el => {
            el.style.background = 'transparent'; el.style.outline = 'none';
          }) });
      } else {
        rendered = document.createElement('canvas');
        rendered.width = Math.ceil(viewport.width); rendered.height = Math.ceil(viewport.height);
        await page.render({ canvasContext: rendered.getContext('2d'), viewport,
          annotationMode: pdfjs.AnnotationMode.ENABLE_STORAGE,
          intent: 'print', printAnnotationStorage: state.pdf.annotationStorage.print }).promise;
        if (shell.qrRect && state.qrImage) {
          const rect = viewportRect(viewport, shell.qrRect);
          const context = rendered.getContext('2d'); context.imageSmoothingEnabled = false;
          context.drawImage(state.qrImage, Math.min(rect[0], rect[2]), Math.min(rect[1], rect[3]),
            Math.abs(rect[2]-rect[0]), Math.abs(rect[3]-rect[1]));
        }
      }
      pages.push({ image: rendered.toDataURL('image/png'), width: viewport.width / 2, height: viewport.height / 2 });
      rendered.width = 0; rendered.height = 0;
    }
    const payload = JSON.stringify({ pages,
      signatures: state.signatures.map(({ id, ...sig }) => sig) });
    if (new Blob([payload]).size > 160 * 1024 * 1024) throw new Error('Export exceeds 160 MB. Save an editable copy instead.');
    const response = await api('sign', { method: 'POST', body: payload, headers: { 'Content-Type': 'application/json' } });
    const path = await writeChosenPDF(destination, new Uint8Array(await response.arrayBuffer()));
    dirty(false);
    notify(`Signed PDF saved\n${path}`);
    status(`Saved signed PDF · ${path}`);
  });
}

const pad = $('signature-pad');
const context = pad.getContext('2d', { willReadFrequently: true });
let drawing = false, lastPoint = null;
function clearPad() {
  context.clearRect(0, 0, pad.width, pad.height);
  state.hasInk = false; $('use-signature').disabled = true;
}
function signatureWizard() {
  clearPad(); state.importedImage = null;
  $('typed-name').value = ''; $('signature-file').value = '';
  signatureMethod('draw');
  $('signature-dialog').showModal();
}
function signatureMethod(method) {
  state.signatureMethod = method;
  for (const choice of ['draw', 'type', 'image']) {
    $(`${choice}-tab`).classList.toggle('active', choice === method);
    $(`${choice}-tab`).setAttribute('aria-selected', choice === method);
  }
  $('type-controls').hidden = method !== 'type'; $('image-controls').hidden = method !== 'image';
  $('signature-description').textContent = { draw: 'Use your mouse, touchpad, or pen.',
    type: 'Type your name and choose a style.', image: 'Import an image of your signature.' }[method];
  clearPad();
  if (method === 'type') { drawTyped(); $('typed-name').focus(); }
  if (method === 'image' && state.importedImage) drawImported();
}

function padPoint(event) {
  const box = pad.getBoundingClientRect();
  return [(event.clientX - box.left) * pad.width / box.width, (event.clientY - box.top) * pad.height / box.height];
}
pad.addEventListener('pointerdown', event => {
  if (state.signatureMethod !== 'draw') return;
  event.preventDefault(); drawing = true; lastPoint = padPoint(event); pad.setPointerCapture(event.pointerId);
  context.fillStyle = '#192323'; context.beginPath(); context.arc(...lastPoint, 2, 0, Math.PI * 2); context.fill();
  state.hasInk = true; $('use-signature').disabled = false;
});
pad.addEventListener('pointermove', event => {
  if (!drawing) return;
  const point = padPoint(event); context.strokeStyle = '#192323'; context.lineWidth = 4;
  context.lineCap = 'round'; context.lineJoin = 'round';
  context.beginPath(); context.moveTo(...lastPoint); context.lineTo(...point); context.stroke(); lastPoint = point;
});
function endStroke() { drawing = false; lastPoint = null; }
pad.addEventListener('pointerup', endStroke); pad.addEventListener('pointercancel', endStroke);

async function drawTyped() {
  await document.fonts.load('100px FormaHandwriting');
  if (state.signatureMethod !== 'type') return;
  const name = $('typed-name').value.trim();
  clearPad();
  if (!name) return;
  const style = $('signature-font').value === 'handwriting' ? '500 130px FormaHandwriting' : 'italic 100px Georgia';
  context.font = style;
  const measure = context.measureText(name).width;
  const scale = Math.min(1, 880 / Math.max(measure, 1));
  context.save(); context.translate(60, 220); context.scale(scale, scale);
  context.fillStyle = '#192323'; context.fillText(name, 0, 0); context.restore();
  state.hasInk = true; $('use-signature').disabled = false;
}

function drawImported() {
  clearPad(); const image = state.importedImage; if (!image) return;
  const scale = Math.min(900 / image.width, 280 / image.height);
  const width = image.width * scale, height = image.height * scale;
  context.drawImage(image, (pad.width - width) / 2, (pad.height - height) / 2, width, height);
  if ($('remove-white').checked) {
    const pixels = context.getImageData(0, 0, pad.width, pad.height);
    for (let i = 0; i < pixels.data.length; i += 4) {
      const lightestInk = Math.min(pixels.data[i], pixels.data[i + 1], pixels.data[i + 2]);
      if (lightestInk > 235) pixels.data[i + 3] = Math.round(pixels.data[i + 3] * (255 - lightestInk) / 20);
    }
    context.putImageData(pixels, 0, 0);
  }
  state.hasInk = true; $('use-signature').disabled = false;
}

async function importSignature(file) {
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) { notify('Choose a signature image under 10 MB.', true); return; }
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) { notify('Choose a PNG, JPG, or WebP image.', true); return; }
  const url = URL.createObjectURL(file);
  try {
    const image = new Image(); image.src = url; await image.decode();
    if (image.width * image.height > 32_000_000) throw new Error('Choose a smaller signature image.');
    state.importedImage = image; drawImported();
  } catch { notify('Could not read that signature image. Try a smaller PNG, JPG, or WebP.', true); }
  finally { URL.revokeObjectURL(url); }
}

function useSignature() {
  const pixels = context.getImageData(0, 0, pad.width, pad.height);
  let minX = pad.width, minY = pad.height, maxX = -1, maxY = -1;
  for (let y = 0; y < pad.height; y++) for (let x = 0; x < pad.width; x++) {
    if (pixels.data[(y * pad.width + x) * 4 + 3] > 12) {
      minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
    }
  }
  if (maxX < 0) { notify('Add a signature first.', true); return; }
  minX = Math.max(0, minX - 8); minY = Math.max(0, minY - 8);
  maxX = Math.min(pad.width - 1, maxX + 8); maxY = Math.min(pad.height - 1, maxY + 8);
  const cropped = document.createElement('canvas'); cropped.width = maxX - minX + 1; cropped.height = maxY - minY + 1;
  cropped.getContext('2d').drawImage(pad, minX, minY, cropped.width, cropped.height, 0, 0, cropped.width, cropped.height);
  state.asset = { image: cropped.toDataURL('image/png'), aspect: cropped.width / cropped.height };
  $('signature-dialog').close(); setMode('sign');
  status('Click the signature box on the form to place your signature. Drag to move; use the corner to resize.');
  notify('Step 2 · Click the page to place your signature.');
}

function placeSignature(page, x, y) {
  const { viewport } = state.pages[page - 1];
  let width = 0.25, height = width * viewport.width / (state.asset.aspect * viewport.height);
  if (height > 0.3) { width *= 0.3 / height; height = 0.3; }
  const sig = { id: crypto.randomUUID(), page, x: Math.max(0, Math.min(1 - width, x - width / 2)),
    y: Math.max(0, Math.min(1 - height, y - height / 2)), width, height, image: state.asset.image };
  state.signatures.push(sig); state.selected = sig.id; dirty();
  renderSignatures(page); updateSignatureList(); updateControls();
  status('Signature placed. Export signed PDF to save it, or add another signature.');
}

function renderSignatures(number) {
  const record = state.pages[number - 1]; if (!record) return;
  record.overlay.replaceChildren();
  for (const sig of state.signatures.filter(s => s.page === number)) {
    const el = document.createElement('div'); el.className = 'signature-placement';
    el.classList.toggle('selected', state.selected === sig.id); el.dataset.signature = sig.id;
    const image = document.createElement('img'); image.src = sig.image; image.alt = 'Placed signature';
    const handle = document.createElement('span'); handle.className = 'resize-handle';
    el.append(image, handle); record.overlay.append(el);
    function position() {
      el.style.left = `${sig.x * 100}%`; el.style.top = `${sig.y * 100}%`;
      el.style.width = `${sig.width * 100}%`; el.style.height = `${sig.height * 100}%`;
    }
    position();
    el.addEventListener('pointerdown', event => {
      if (state.busy || state.mode !== 'sign') return;
      event.preventDefault(); event.stopPropagation();
      state.selected = sig.id;
      document.querySelectorAll('.signature-placement').forEach(p => p.classList.toggle('selected', p === el));
      const resize = event.target === handle;
      const startX = event.clientX, startY = event.clientY, initial = { ...sig };
      const box = record.overlay.getBoundingClientRect();
      el.setPointerCapture(event.pointerId);
      const move = e => {
        if (resize) {
          const ratio = initial.height / initial.width;
          const maxWidth = Math.min(1 - sig.x, (1 - sig.y) / ratio);
          sig.width = Math.max(Math.min(0.025, maxWidth), Math.min(maxWidth, initial.width + (e.clientX - startX) / box.width));
          sig.height = sig.width * ratio;
        } else {
          sig.x = Math.max(0, Math.min(1 - sig.width, initial.x + (e.clientX - startX) / box.width));
          sig.y = Math.max(0, Math.min(1 - sig.height, initial.y + (e.clientY - startY) / box.height));
        }
        position(); dirty();
      };
      const stop = () => { el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', stop); el.removeEventListener('pointercancel', stop); };
      el.addEventListener('pointermove', move); el.addEventListener('pointerup', stop); el.addEventListener('pointercancel', stop);
    });
  }
}

function removeSignature(id) {
  if (state.busy) return;
  const sig = state.signatures.find(s => s.id === id); if (!sig) return;
  state.signatures = state.signatures.filter(s => s.id !== id); state.selected = null;
  renderSignatures(sig.page); updateSignatureList(); updateControls(); dirty();
}
function updateSignatureList() {
  $('signature-list').hidden = !state.signatures.length;
  $('signatures').replaceChildren();
  state.signatures.forEach((sig, i) => {
    const button = document.createElement('button'); button.textContent = `Signature ${i + 1} · Page ${sig.page}   ×`;
    button.title = 'Remove this signature'; button.addEventListener('click', () => removeSignature(sig.id));
    $('signatures').append(button);
  });
}

function chooseFile() { if (!state.busy) $('file').click(); }
$('open').addEventListener('click', chooseFile);
$('drop-zone').addEventListener('click', chooseFile);
$('drop-zone').addEventListener('keydown', event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); chooseFile(); } });
$('file').addEventListener('change', async event => {
  const file = event.target.files[0]; if (file && !state.busy) await openBytes(new Uint8Array(await file.arrayBuffer()), file.name);
  event.target.value = '';
});
window.addEventListener('dragover', event => { event.preventDefault(); $('drop-zone').classList.add('dragging'); });
window.addEventListener('dragleave', () => $('drop-zone').classList.remove('dragging'));
window.addEventListener('drop', async event => {
  event.preventDefault(); $('drop-zone').classList.remove('dragging');
  const file = event.dataTransfer.files[0];
  if (!file || state.busy) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) { notify('Choose a PDF file.', true); return; }
  await openBytes(new Uint8Array(await file.arrayBuffer()), file.name);
});
$('fill-tool').addEventListener('click', () => setMode('fill'));
$('sign-tool').addEventListener('click', () => { if (state.asset) setMode('sign'); else signatureWizard(); });
$('new-signature').addEventListener('click', signatureWizard);
$('next-field').addEventListener('pointerdown', event => { event.preventDefault(); nextField(); });
$('next-field').addEventListener('click', event => { if (event.detail === 0) nextField(); });
$('save').addEventListener('click', saveEditable); $('export').addEventListener('click', exportSigned);
$('current-page').addEventListener('change', event => goToPage(Number(event.target.value)));
$('prev-page').addEventListener('click', () => goToPage(Number($('current-page').value) - 1));
$('next-page').addEventListener('click', () => goToPage(Number($('current-page').value) + 1));
async function changeZoom(value) {
  if (!state.pdf) return;
  const number = Number($('current-page').value);
  $('zoom').value = value;
  await work('Adjusting the view…', async () => { await renderPages(); goToPage(number); status('View updated. Your edits are preserved.'); });
}
$('zoom').addEventListener('change', event => changeZoom(event.target.value));
const zoomLevels = [0.75, 1, 1.25, 1.5, 2];
$('zoom-in').addEventListener('click', () => changeZoom(String(zoomLevels.find(z => z > state.scale * 72 / 96 + .01) || 2)));
$('zoom-out').addEventListener('click', () => changeZoom(String([...zoomLevels].reverse().find(z => z < state.scale * 72 / 96 - .01) || .75)));
$('about').addEventListener('click', () => $('about-dialog').showModal());
$('save-up').addEventListener('click', () => browseSaveFolder($('save-up').dataset.folder));
$('save-home').addEventListener('click', () => browseSaveFolder($('save-home').dataset.folder));
$('save-folder').addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); browseSaveFolder(event.target.value); }
});
$('go-save-folder').addEventListener('click', () => browseSaveFolder($('save-folder').value));
$('cancel-save').addEventListener('click', () => finishSaveDialog(null));
$('save-dialog').addEventListener('cancel', event => { event.preventDefault(); finishSaveDialog(null); });
$('save-form').addEventListener('submit', event => {
  event.preventDefault();
  if ($('confirm-save').disabled || !saveDialogFolder) return;
  let filename = $('save-filename').value.trim();
  if (!filename || /[/\\\x00-\x1f]/.test(filename) || ['.', '..'].includes(filename)) {
    $('save-dialog-error').textContent = 'Enter a filename without folder separators.'; return;
  }
  if (!filename.toLowerCase().endsWith('.pdf')) filename += '.pdf';
  finishSaveDialog(`${saveDialogFolder.replace(/\/$/, '')}/${filename}`);
});
document.querySelector('.brand').addEventListener('click', event => { event.preventDefault(); $('about-dialog').showModal(); });
for (const method of ['draw', 'type', 'image']) $(`${method}-tab`).addEventListener('click', () => signatureMethod(method));
$('typed-name').addEventListener('input', drawTyped); $('signature-font').addEventListener('change', drawTyped);
$('choose-image').addEventListener('click', () => $('signature-file').click());
$('signature-file').addEventListener('change', event => importSignature(event.target.files[0]));
$('remove-white').addEventListener('change', drawImported);
$('clear-signature').addEventListener('click', () => { clearPad(); $('typed-name').value = ''; state.importedImage = null; $('signature-file').value = ''; });
$('cancel-signature').addEventListener('click', () => $('signature-dialog').close());
$('use-signature').addEventListener('click', useSignature);
$('password-form').addEventListener('submit', event => { event.preventDefault(); passwordCallback?.($('password').value); $('password-dialog').close(); });
function cancelPassword() { passwordReject?.(); $('password-dialog').close(); }
$('cancel-password').addEventListener('click', cancelPassword);
$('password-dialog').addEventListener('cancel', event => { event.preventDefault(); cancelPassword(); });
window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('keydown', event => {
  if (event.ctrlKey && event.key.toLowerCase() === 'o') { event.preventDefault(); chooseFile(); }
  if (event.ctrlKey && event.key.toLowerCase() === 's') { event.preventDefault(); if (!state.busy) saveEditable(); }
  if (event.key === 'Delete' && state.selected && !['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) removeSignature(state.selected);
  if (event.key === 'Escape' && !$('signature-dialog').open && !state.busy) setMode('fill');
});
window.forma = { get ready() { return !!state.pdf && !state.busy; }, get pageCount() { return state.pages.length; },
  get signatureCount() { return state.signatures.length; }, get dirty() { return state.dirty; },
  get initialized() { return state.session !== null; } };

try {
  state.session = await (await api('session')).json();
  if (state.session.source) await openBytes(new Uint8Array(await (await api('source')).arrayBuffer()), state.session.source);
} catch (error) { notify(`${error.message} Reopen Forma from its launcher.`, true); }
