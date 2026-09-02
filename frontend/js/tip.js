/* VisTest - self-hosted visual regression testing.
 * Copyright (C) 2026 Kirill Kulagin
 * SPDX-License-Identifier: AGPL-3.0-or-later
 *
 * This file is part of VisTest. See LICENSE for the full terms and NOTICE for
 * the trademark and commercial-licensing terms. Removing this header does not
 * remove those obligations.
 */

/* Подсказки: `data-tip="…"` на любом элементе.

   Зачем свои, когда есть `title`. Интерфейс приборный: половина того, что в
   нём написано, — это термины, у которых есть точный смысл (severity, ΔE00,
   SSIM, «no longer checked», «own set»), и человек, впервые их увидевший,
   должен уметь спросить «а это что» прямо здесь. Браузерный `title` на эту
   роль не годится:

   * он появляется через ~1 с и исчезает через ~5 — то есть ровно тогда, когда
     мышь уже уехала, и ровно до того, как длинное объяснение дочитано;
   * от клавиатуры он не показывается никогда, а половина навигации здесь
     проходится Tab'ом;
   * рисует его операционная система: чужой шрифт, чужой цвет, чужая рамка
     посреди сетки, собранной на одном пикселе границы.

   Правила, по которым это сделано:

   * **Узел один на весь документ.** Подсказки висят на сотнях ячеек, которые
     пересобираются на каждом обновлении счётчиков; заводить узел или слушателя
     на каждую значило бы копить их до конца сессии. Слушатели — делегированные,
     на `document`.
   * **То же самое от клавиатуры.** `focusin` показывает подсказку так же, как
     `mouseover`. Иначе объяснение терминов есть только у тех, кто работает
     мышью.
   * **`aria-describedby` на время показа.** Скринридер читает подсказку как
     описание элемента — то есть после его имени, а не вместо.
   * **Escape закрывает.** Подсказка перекрывает соседнюю строку таблицы; из
     всего, что перекрывает содержимое, должен быть выход клавишей.

   Текст подсказки — обычный текст, не разметка: он попадает в `textContent`.
   Это не ограничение, а защита — `data-tip` собирается в шаблонных строках
   рядом с данными от сервера. */

const TIP_MAX = 300;   /* ширина плашки, px — совпадает с CSS */
const TIP_GAP = 11;    /* зазор до элемента, px */

let tipEl = null, tipFor = null, tipTimer = null;

function tipNode(){
  if(!tipEl)tipEl=document.getElementById('tip');
  return tipEl;
}

/* Показать подсказку у элемента. `instant` — для клавиатуры: там задержка
   бессмысленна, фокус ставят намеренно. */
function showTip(host, instant){
  const box=tipNode(), text=host&&host.getAttribute('data-tip');
  if(!box||!text)return;
  clearTimeout(tipTimer);
  const paint=()=>{
    /* Элемент мог исчезнуть за время задержки: экраны пересобираются целиком,
       и подсказка от прошлой отрисовки повисла бы над пустым местом. */
    if(!host.isConnected)return hideTip();
    box.textContent=text;
    box.classList.add('on');
    box.setAttribute('aria-hidden','false');
    placeTip(host, box);
    /* Имя узла подсказки постоянно, поэтому связь ставится и снимается, а не
       накапливается: иначе элемент уносит с собой ссылку на чужое описание. */
    host.setAttribute('aria-describedby','tip');
    tipFor=host;
  };
  if(instant)paint();else tipTimer=setTimeout(paint,140);
}

/* Плашка ставится над элементом, а под ним — только когда сверху не помещается.
   Порядок именно такой: снизу подсказка накрывает следующую строку таблицы,
   то есть ровно то, что человек в этот момент сравнивает глазами. */
function placeTip(host, box){
  const r=host.getBoundingClientRect();
  const h=box.offsetHeight||36;
  const above=r.top>h+TIP_GAP+6;
  box.classList.toggle('above',above);
  box.classList.toggle('below',!above);
  /* По горизонтали плашка центрируется по элементу, но не вылезает за окно:
     у правого края таблицы объяснение иначе обрезается ровно по середине. */
  const half=Math.min(TIP_MAX,box.offsetWidth||TIP_MAX)/2+8;
  box.style.left=Math.round(Math.min(Math.max(r.left+r.width/2,half),
                                     window.innerWidth-half))+'px';
  box.style.top=Math.round(above?r.top-TIP_GAP:r.bottom+TIP_GAP)+'px';
}

function hideTip(){
  clearTimeout(tipTimer);
  const box=tipNode();
  if(box){box.classList.remove('on');box.setAttribute('aria-hidden','true');}
  if(tipFor){tipFor.removeAttribute('aria-describedby');tipFor=null;}
}

/* `title` подхватывается тем же механизмом.

   В интерфейсе четыре десятка мест, где объяснение написано в `title=`, и они
   пишутся дальше — это первое, что приходит в голову, когда надо что-то
   пояснить. Переписать их разом можно, а удержать переписанными нельзя:
   следующая правка добавит сорок первое, и в интерфейсе окажется два вида
   подсказок с разной задержкой и разным видом.

   Поэтому `title` не запрещается, а усыновляется: при первом же наведении его
   текст переезжает в `data-tip`, а сам атрибут снимается — иначе браузер
   покажет поверх нашей плашки ещё и свою.

   Отдельно про имя: у кнопки без текста (крестик, «⋯») `title` был
   ЕДИНСТВЕННЫМ, что называло её скринридеру. Описание именем не является, и
   унести его в `data-tip`, ничего не оставив взамен, значило бы сделать кнопку
   безымянной. Поэтому там, где текста внутри нет, тот же текст ставится
   `aria-label`. */
function adoptTitle(node){
  const t=node.getAttribute('title');
  if(t==null||t==='')return;
  node.setAttribute('data-tip',t);
  if(!node.getAttribute('aria-label')&&!String(node.textContent||'').trim())
    node.setAttribute('aria-label',t);
  node.removeAttribute('title');
}

function tipHost(node){
  if(!node||!node.closest)return null;
  const withTitle=node.closest('[title]');
  if(withTitle)adoptTitle(withTitle);
  return node.closest('[data-tip]');
}

document.addEventListener('mouseover',e=>{
  const host=tipHost(e.target);
  if(host===tipFor)return;
  if(host)showTip(host,false);else hideTip();
});
document.addEventListener('mouseout',e=>{
  if(!tipHost(e.relatedTarget))hideTip();
});
/* Нажатие гасит подсказку всегда: щёлкнули — значит уже решили, а плашка
   осталась бы висеть над экраном, который под ней сменился. */
document.addEventListener('mousedown',hideTip,true);
document.addEventListener('focusin',e=>{
  const host=tipHost(e.target);
  if(host)showTip(host,true);else hideTip();
});
document.addEventListener('focusout',hideTip);
document.addEventListener('keydown',e=>{if(e.key==='Escape')hideTip();});
/* Прокрутка и изменение размера: плашка привязана к координатам на экране, а
   не к элементу, — оставить её на месте значит показать объяснение к другой
   строке. Проще погасить, чем пересчитывать на каждый кадр. */
window.addEventListener('scroll',hideTip,true);
window.addEventListener('resize',hideTip);

/* Навесить подсказку из кода — там, где узел собран через `el()`, а не
   шаблонной строкой. Возвращает сам узел, чтобы вставать в цепочку. */
function tip(node,text){
  if(node&&text)node.setAttribute('data-tip',text);
  return node;
}
