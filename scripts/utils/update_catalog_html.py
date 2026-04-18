"""
Генератор обновлённого catalog.html с:
- Карточками категорий с иконками
- Древовидным меню
- Хлебными крошками  
- Фото товаров
"""

html_content = r'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="csrf-token" content="{{ csrf_token }}">
    <title>Каталог</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; color: #333; }
        .header { background: linear-gradient(135deg, #2E75B6, #1E5A8E); color: white; padding: 12px 20px; display: flex; justify-content: space-between; align-items: center; }
        .header h1 { font-size: 18px; }
        .btn { padding: 7px 14px; border: none; border-radius: 5px; cursor: pointer; font-size: 13px; font-weight: 600; color: white; text-decoration: none; }
        .btn-back { background: rgba(255,255,255,0.2); }
        .btn-add { background: #4CAF50; }

        .container { max-width: 98vw; margin: 0 auto; padding: 15px; }

        .tabs { display: flex; gap: 5px; margin-bottom: 15px; }
        .tab { padding: 8px 18px; border-radius: 5px; cursor: pointer; font-weight: 600; border: none; background: #ddd; color: #555; }
        .tab.active { background: #2E75B6; color: white; }

        /* Карточки главных категорий */
        .cat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; margin-bottom: 20px; }
        .cat-card { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px; border-radius: 10px; cursor: pointer; transition: all 0.2s; text-align: center; }
        .cat-card:hover { transform: translateY(-3px); box-shadow: 0 5px 15px rgba(0,0,0,0.2); }
        .cat-card .cat-icon { font-size: 32px; margin-bottom: 8px; }
        .cat-card .cat-name { font-weight: 600; font-size: 14px; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .cat-card .cat-count-badge { font-size: 12px; opacity: 0.8; }
        .cat-card:nth-child(2n) { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }
        .cat-card:nth-child(3n) { background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); }
        .cat-card:nth-child(4n) { background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%); }
        .cat-card:nth-child(5n) { background: linear-gradient(135deg, #fa709a 0%, #fee140 100%); }

        /* Хлебные крошки */
        .breadcrumbs { padding: 10px 15px; background: white; border-radius: 8px; margin-bottom: 15px; box-shadow: 0 2px 6px rgba(0,0,0,0.06); font-size: 13px; }
        .breadcrumbs a { color: #2E75B6; text-decoration: none; }
        .breadcrumbs a:hover { text-decoration: underline; }
        .breadcrumbs span { color: #666; }
        .breadcrumbs .sep { margin: 0 5px; color: #999; }

        .layout { display: grid; grid-template-columns: 280px 1fr; gap: 15px; }
        @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }

        .sidebar { background: white; border-radius: 8px; padding: 10px; box-shadow: 0 2px 6px rgba(0,0,0,0.06); max-height: 80vh; overflow-y: auto; position: sticky; top: 10px; }
        .sidebar h3 { font-size: 14px; color: #2E75B6; margin-bottom: 8px; padding: 5px; }
        .srch { width: 100%; padding: 7px; border: 1px solid #ddd; border-radius: 5px; margin-bottom: 8px; font-size: 13px; }
        .cat-item { padding: 5px 8px; cursor: pointer; border-radius: 4px; font-size: 12px; display: flex; justify-content: space-between; }
        .cat-item:hover { background: #e3f2fd; }
        .cat-item.active { background: #2E75B6; color: white; }
        .cat-count { font-size: 11px; color: #999; }
        .cat-item.active .cat-count { color: rgba(255,255,255,0.7); }

        /* Древовидное меню */
        .tree-view { margin-top: 10px; }
        .tree-node { margin-left: 0; }
        .tree-node .tree-node { margin-left: 18px; }
        .tree-toggle { display: inline-block; width: 20px; cursor: pointer; transition: transform 0.2s; color: #666; font-size: 11px; text-align: center; }
        .tree-toggle.expanded { transform: rotate(90deg); }
        .tree-toggle.leaf { visibility: hidden; }
        .tree-label { padding: 3px 6px; cursor: pointer; border-radius: 3px; font-size: 12px; display: inline-block; }
        .tree-label:hover { background: #e3f2fd; }
        .tree-label.active { background: #2E75B6; color: white; }
        .tree-children { display: none; }
        .tree-children.open { display: block; }

        .content { background: white; border-radius: 8px; padding: 15px; box-shadow: 0 2px 6px rgba(0,0,0,0.06); }
        .content-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 8px; }
        .content-header h2 { font-size: 16px; color: #2E75B6; }

        .tbl-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 12px; min-width: 900px; }
        th { background: #2E75B6; color: white; padding: 7px 8px; text-align: left; position: sticky; top: 0; white-space: nowrap; }
        td { padding: 6px 8px; border-bottom: 1px solid #eee; vertical-align: top; }
        tr:hover { background: #f5f5f5; }
        .name-cell { font-weight: 600; max-width: 250px; }
        .price { font-weight: 600; color: #2E75B6; white-space: nowrap; }
        .profit { color: #4CAF50; font-weight: 600; white-space: nowrap; }
        .opt { color: #FF9800; font-size: 11px; white-space: nowrap; }
        .brand-cell { font-size: 11px; color: #888; white-space: nowrap; }
        .art-cell { font-size: 11px; color: #aaa; white-space: nowrap; }
        .act-btn { width: 24px; height: 24px; border: none; border-radius: 3px; cursor: pointer; color: white; font-size: 12px; }
        .act-edit { background: #FF9800; }
        .act-del { background: #f44336; }
        .empty { text-align: center; padding: 30px; color: #999; }

        /* Фото товара */
        .prod-img { width: 50px; height: 50px; object-fit: contain; border-radius: 4px; background: #f5f5f5; }
        .prod-img-placeholder { width: 50px; height: 50px; display: flex; align-items: center; justify-content: center; background: #f0f2f5; border-radius: 4px; font-size: 24px; }

        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 1000; align-items: center; justify-content: center; }
        .modal.active { display: flex; }
        .modal-content { background: white; padding: 20px; border-radius: 8px; width: 90vw; max-width: 800px; max-height: 90vh; overflow-y: auto; }
        .fg { margin-bottom: 8px; }
        .fg label { display: block; font-size: 11px; font-weight: 600; margin-bottom: 2px; }
        .fg input, .fg select { width: 100%; padding: 6px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px; }
        .frow { display: flex; gap: 8px; }
        .frow .fg { flex: 1; }
        .modal-actions { display: flex; gap: 10px; margin-top: 12px; justify-content: flex-end; }

        .hidden { display: none; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Каталог</h1>
        <a href="/" class="btn btn-back">На главную</a>
    </div>

    <div class="container">
        <div class="tabs">
            <button class="tab active" onclick="switchType('mat')">Материалы</button>
            <button class="tab" onclick="switchType('work')">Работы / Услуги</button>
        </div>

        <!-- Карточки главных категорий (только для материалов) -->
        <div id="catGrid" class="cat-grid"></div>

        <!-- Хлебные крошки -->
        <nav class="breadcrumbs hidden" id="breadcrumbs">
            <a href="#" onclick="resetDrill(); return false;">Все категории</a>
        </nav>

        <div class="layout">
            <div class="sidebar" id="sidebar">
                <input type="text" class="srch" id="cat-srch" placeholder="Поиск категории...">
                <h3 id="sb-title">Категории</h3>
                <div id="cat-list"></div>
                <div id="tree-view" class="tree-view"></div>
            </div>

            <div class="content">
                <div class="content-header">
                    <h2 id="ct-title">Все материалы</h2>
                    <button class="btn btn-add" onclick="openAdd()">+ Добавить</button>
                </div>
                <input type="text" class="srch" id="item-srch" placeholder="Поиск по названию, артикулу, бренду...">
                <div class="tbl-wrap">
                    <table>
                        <thead><tr>
                            <th>№</th>
                            <th>Фото</th>
                            <th id="th-art">Артикул</th>
                            <th id="th-brand">Бренд</th>
                            <th>Название</th>
                            <th>Тип</th>
                            <th>Ед</th>
                            <th id="th-purch">Закупка</th>
                            <th id="th-retail">Цена</th>
                            <th id="th-profit">Прибыль</th>
                            <th>Действия</th>
                        </tr></thead>
                        <tbody id="tbl"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <div class="modal" id="editModal">
        <div class="modal-content">
            <h2 id="mTitle" style="margin-bottom:12px;color:#2E75B6;">Добавить</h2>
            <input type="hidden" id="f-id">
            <div class="fg"><label>Артикул</label><input type="text" id="f-art"></div>
            <div class="fg"><label>Название</label><input type="text" id="f-name"></div>
            <div class="frow">
                <div class="fg" style="flex:0.5"><label>Ед</label>
                    <select id="f-unit"><option>шт</option><option>м</option><option>м²</option><option>м³</option><option>кг</option><option>т</option><option>л</option><option>компл</option><option>усл</option><option>ч</option></select>
                </div>
                <div class="fg"><label>Бренд</label><input type="text" id="f-brand"></div>
                <div class="fg"><label>Категория</label><input type="text" id="f-cat"></div>
            </div>
            <div class="frow" id="pRowMat">
                <div class="fg"><label>Закупка</label><input type="number" step="0.01" id="f-purch"></div>
                <div class="fg"><label>РРЦ</label><input type="number" step="0.01" id="f-retail"></div>
            </div>
            <div class="fg" id="pRowWork" style="display:none;"><label>Цена</label><input type="number" step="0.01" id="f-wprice"></div>
            <div class="fg"><label>Тип</label><input type="text" id="f-type"></div>
            <div class="modal-actions">
                <button class="btn" style="background:#999" onclick="closeModal()">Отмена</button>
                <button class="btn btn-add" onclick="saveItem()">Сохранить</button>
            </div>
        </div>
    </div>

    <script>
        let curType='mat', curCat='', allData=[], categoryTree=[], currentPath=[];

        function csrf(){ const m=document.querySelector('meta[name="csrf-token"]'); return m?m.getAttribute('content'):''; }
        function money(v){ return Number(v||0).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}); }
        function esc(s){ if(!s) return ''; const d=document.createElement('div'); d.textContent=String(s); return d.innerHTML; }

        // Иконки для категорий
        function getCatIcon(name) {
            const icons = {
                'насос': '💧', 'труб': '🔧', 'фитинг': '🔩', 'кран': '🚰', 'вентил': '🔴',
                'клапан': '⚙️', 'фильтр': '🔬', 'водонагреват': '🔥', 'отоплен': '🌡️',
                'тёпл': '🏠', 'тепл': '🏠', 'канализац': '🚽', 'мембран': '🫧', 'бак': '🪣',
                'кабел': '🔌', 'автоматик': '🤖', 'запчаст': '🔧', 'гайк': '🔩', 'хомут': '🔗',
                'уплотн': '📦', 'муфт': '🔗', 'шланг': '🐍', 'радиатор': '🌡️', 'панел': '🖼️',
                'полотенцесушит': '🛁', 'терм': '🌡️', 'электр': '⚡', 'полипропилен': '🔵',
                'обратн': '↩️', 'манометр': '📊', 'люк': '🚪', 'головк': '🔲', 'обсадн': '🔲',
                'околодец': '🕳️', 'душ': '🚿', 'гибк': '〰️', 'групп': '📋', 'демпфер': '📏',
                'креплен': '📌', 'защитн': '🛡️', 'фольга': '✨', 'шкаф': '🗄️', 'коллектор': '🔀',
                'гидравл': '⚙️', 'двигател': '⚡', 'диффузор': '💨', 'измельчител': '🔄',
                'комплект': '📦', 'консол': '📐', 'корпус': '📦', 'крыльчатк': '🌀',
                'направл': '➡️', 'рабоч': '⚙️', 'сальник': '🔘', 'ремонт': '🔧', 'статор': '⚡',
                'ротор': '⚡', 'штуцер': '🔗', 'лист': '📄', 'запасн': '📦', 'повысит': '📈',
                'бассейн': '🏊', 'воздушн': '💨', 'фонтан': '⛲', 'ручеек': '🌊', 'скважин': '🕳️',
                'фекальн': '💩', 'насосн': '🔧', 'колодезн': '🕳️', 'дренажн': '💦',
                'поверхностн': '🔧', 'шламов': '⛏️', 'циркуляцион': '🔄', 'конвектор': '🌡️'
            };
            const n = (name||'').toLowerCase();
            for (const [key, icon] of Object.entries(icons)) {
                if (n.includes(key)) return icon;
            }
            return '📦';
        }

        function switchType(t){
            curType=t; curCat=''; currentPath=[];
            document.querySelectorAll('.tab').forEach((el,i)=>el.classList.toggle('active',(t==='mat'&&i===0)||(t==='work'&&i===1)));
            document.getElementById('item-srch').value='';
            loadData();
        }

        async function loadData(){
            const urlMat='/estimate/api/catalog/materials';
            const urlWork='/estimate/api/catalog/works';
            const urlTree='/estimate/api/catalog/categories/tree';
            try{
                // Загружаем данные параллельно
                const [dataRes, treeRes] = await Promise.all([
                    fetch(curType==='mat'?urlMat:urlWork),
                    curType==='mat' ? fetch(urlTree+'?type=material') : Promise.resolve(null)
                ]);
                
                if(!dataRes.ok) throw new Error('HTTP '+dataRes.status);
                allData=await dataRes.json();
                
                // Загружаем дерево категорий
                if(treeRes && treeRes.ok) {
                    categoryTree = await treeRes.json();
                }
                
                renderCatGrid();
                renderTreeView();
                renderItems();
            }catch(e){
                document.getElementById('cat-list').innerHTML='<div class="empty">Ошибка</div>';
                document.getElementById('tbl').innerHTML='<tr><td colspan="12" class="empty">'+esc(e.message)+'</td></tr>';
            }
        }

        // Отображение карточек главных категорий
        function renderCatGrid(){
            const grid = document.getElementById('catGrid');
            if(curType==='work' || !categoryTree.length) {
                grid.innerHTML = '';
                return;
            }
            
            // Считаем материалы по категориям
            const counts = {};
            allData.forEach(d => { counts[d.category] = (counts[d.category]||0)+1; });
            
            let html = '';
            categoryTree.forEach(cat => {
                // Суммируем количество включая подкатегории
                let total = counts[cat.name] || 0;
                cat.children?.forEach(child => { total += counts[child.name] || 0; });
                
                html += `<div class="cat-card" onclick="drillDown('${esc(cat.name)}')">
                    <div class="cat-icon">${getCatIcon(cat.name)}</div>
                    <div class="cat-name">${esc(cat.name)}</div>
                    <div class="cat-count-badge">${total} товар(ов)</div>
                </div>`;
            });
            grid.innerHTML = html;
        }

        // Древовидное представление в сайдбаре
        function renderTreeView(){
            const treeContainer = document.getElementById('tree-view');
            const flatList = document.getElementById('cat-list');
            
            if(curType==='work' || !categoryTree.length) {
                treeContainer.innerHTML = '';
                // Для работ показываем плоский список
                renderFlatCategories();
                return;
            }
            
            flatList.innerHTML = '';  // Скрываем плоский список
            treeContainer.innerHTML = renderTreeNodes(categoryTree, 0);
        }

        function renderTreeNodes(nodes, depth) {
            if(!nodes || !nodes.length) return '';
            let html = '';
            nodes.forEach(node => {
                const hasChildren = node.children && node.children.length > 0;
                const isActive = curCat === node.name;
                const toggleClass = hasChildren ? 'tree-toggle' : 'tree-toggle leaf';
                const labelClass = isActive ? 'tree-label active' : 'tree-label';
                
                html += `<div class="tree-node">
                    <span class="${toggleClass}" onclick="toggleTreeNode(this)">&#9654;</span>
                    <span class="${labelClass}" onclick="selectCategory('${esc(node.name).replace(/'/g,"\\'")}')">${getCatIcon(node.name)} ${esc(node.name)}</span>
                    ${hasChildren ? `<div class="tree-children">${renderTreeNodes(node.children, depth+1)}</div>` : ''}
                </div>`;
            });
            return html;
        }

        function toggleTreeNode(el) {
            if(el.classList.contains('leaf')) return;
            el.classList.toggle('expanded');
            const childrenDiv = el.parentElement.querySelector('.tree-children');
            if(childrenDiv) childrenDiv.classList.toggle('open');
        }

        function renderFlatCategories(){
            const box=document.getElementById('cat-list');
            const counts={};
            allData.forEach(function(d){ counts[d.category]=(counts[d.category]||0)+1; });
            var cats=Object.keys(counts).sort();
            var html='<div class="cat-item'+(curCat===''?' active':'')+'" onclick="selCat(\'\')"><span>Все</span><span class="cat-count">'+allData.length+'</span></div>';
            cats.forEach(function(c){
                html+='<div class="cat-item'+(curCat===c?' active':'')+'" onclick="selCat(\''+esc(c).replace(/'/g,"\\'")+'\')"><span>'+esc(c)+'</span><span class="cat-count">'+counts[c]+'</span></div>';
            });
            box.innerHTML=html;
        }

        // Drill-down навигация
        function drillDown(catName) {
            currentPath = [catName];
            curCat = catName;
            updateBreadcrumbs();
            renderItems();
            
            // Подсвечиваем в дереве
            document.querySelectorAll('.tree-label').forEach(el => {
                el.classList.toggle('active', el.textContent.includes(catName));
            });
            
            // Скроллим к таблице
            document.querySelector('.tbl-wrap').scrollIntoView({behavior: 'smooth'});
        }

        function updateBreadcrumbs() {
            const bc = document.getElementById('breadcrumbs');
            if(!currentPath.length) {
                bc.classList.add('hidden');
                return;
            }
            
            bc.classList.remove('hidden');
            let html = '<a href="#" onclick="resetDrill(); return false;">Все категории</a>';
            currentPath.forEach((cat, i) => {
                html += '<span class="sep">→</span>';
                if(i === currentPath.length - 1) {
                    html += '<span>'+getCatIcon(cat)+' '+esc(cat)+'</span>';
                } else {
                    html += '<a href="#" onclick="drillTo('+i+'); return false;">'+esc(cat)+'</a>';
                }
            });
            bc.innerHTML = html;
        }

        function resetDrill() {
            currentPath = [];
            curCat = '';
            document.getElementById('item-srch').value = '';
            updateBreadcrumbs();
            renderItems();
            document.querySelectorAll('.tree-label').forEach(el => el.classList.remove('active'));
        }

        function drillTo(index) {
            currentPath = currentPath.slice(0, index + 1);
            curCat = currentPath[currentPath.length - 1];
            updateBreadcrumbs();
            renderItems();
        }

        function selCat(c){ curCat=c; document.getElementById('item-srch').value=''; renderItems(); }

        document.getElementById('cat-srch').addEventListener('input',function(){
            var q=this.value.toLowerCase();
            document.querySelectorAll('.cat-item').forEach(function(el){ el.style.display=el.textContent.toLowerCase().includes(q)?'':'none'; });
            // Поиск по дереву
            document.querySelectorAll('.tree-label').forEach(function(el){
                el.style.display = el.textContent.toLowerCase().includes(q) ? '' : 'none';
            });
        });

        // Debounce для поиска
        var catalogSearchTimeout = null;
        document.getElementById('item-srch').addEventListener('input', function() {
            clearTimeout(catalogSearchTimeout);
            catalogSearchTimeout = setTimeout(function() { renderItems(); }, 150);
        });

        function renderItems(){
            var q=(document.getElementById('item-srch').value||'').toLowerCase().trim();
            var items=allData;
            if(curCat) items=items.filter(function(d){return d.category===curCat;});
            if(q){
                var words = q.split(/\s+/).filter(function(w){ return w.length >= 1; });
                items=items.filter(function(d){
                    var name = (d.name||'').toLowerCase();
                    var article = (d.article||'').toLowerCase();
                    var brand = (d.brand||'').toLowerCase();
                    var cat = (d.category||'').toLowerCase();
                    var desc = (d.description||'').toLowerCase();
                    var searchIn = name + ' ' + article + ' ' + brand + ' ' + cat + ' ' + desc;
                    for (var i = 0; i < words.length; i++) {
                        if (searchIn.indexOf(words[i]) === -1) return false;
                    }
                    return true;
                });
                items.sort(function(a, b) {
                    var aName = a.name.toLowerCase();
                    var bName = b.name.toLowerCase();
                    var aInName = words.every(function(w){ return aName.indexOf(w) !== -1; });
                    var bInName = words.every(function(w){ return bName.indexOf(w) !== -1; });
                    if (aInName && !bInName) return -1;
                    if (bInName && !aInName) return 1;
                    return a.name.localeCompare(b.name);
                });
            }

            document.getElementById('ct-title').textContent=curCat||'Все '+(curType==='mat'?'материалы':'услуги');
            document.getElementById('sb-title').textContent=curType==='mat'?'Категории материалов':'Категории услуг';

            var isWork = curType==='work';
            document.getElementById('th-art').style.display=isWork?'none':'';
            document.getElementById('th-brand').style.display=isWork?'none':'';
            document.getElementById('th-purch').style.display=isWork?'none':'';
            document.getElementById('th-retail').textContent=isWork?'Цена':'РРЦ';
            document.getElementById('pRowMat').style.display=isWork?'none':'flex';
            document.getElementById('pRowWork').style.display=isWork?'block':'none';

            var tb=document.getElementById('tbl');
            if(!items.length){ tb.innerHTML='<tr><td colspan="11" class="empty">Нет товаров</td></tr>'; return; }
            var html='';
            items.forEach(function(d,i){
                var purch=d.purchase_price||0;
                var retail=d.retail_price||d.price||0;
                var profit=retail-purch;
                var imgHtml = '<div class="prod-img-placeholder">📦</div>';
                if(d.image_path) {
                    imgHtml = '<img class="prod-img" src="/static/'+d.image_path+'" alt="">';
                }
                html+='<tr>'+
                    '<td>'+(i+1)+'</td>'+
                    '<td>'+imgHtml+'</td>'+
                    '<td class="art-cell"'+(isWork?' style="display:none"':'')+'>'+esc(d.article)+'</td>'+
                    '<td class="brand-cell"'+(isWork?' style="display:none"':'')+'>'+esc(d.brand)+'</td>'+
                    '<td class="name-cell">'+esc(d.name)+'</td>'+
                    '<td style="font-size:11px;color:#888;">'+esc(d.item_type)+'</td>'+
                    '<td>'+esc(d.unit)+'</td>'+
                    '<td class="price"'+(isWork?' style="display:none"':'')+'>'+money(purch)+'</td>'+
                    '<td class="price">'+money(retail)+'</td>'+
                    '<td class="profit"'+(isWork?' style="display:none"':'')+'>'+(isWork?'':'+')+money(profit)+'</td>'+
                    '<td><button class="act-btn act-edit" onclick="editItem('+d.id+')">&#9998;</button> '+
                        '<button class="act-btn act-del" onclick="delItem('+d.id+')">&#10005;</button></td></tr>';
            });
            tb.innerHTML=html;
        }

        function openAdd(){
            document.getElementById('mTitle').textContent='Добавить';
            ['f-id','f-art','f-name','f-brand','f-cat','f-type','f-purch','f-retail','f-wprice'].forEach(function(id){ document.getElementById(id).value=''; });
            document.getElementById('f-unit').value='шт';
            document.getElementById('f-cat').value=curCat||'';
            document.getElementById('pRowMat').style.display=curType==='mat'?'flex':'none';
            document.getElementById('pRowWork').style.display=curType==='work'?'block':'none';
            document.getElementById('editModal').classList.add('active');
        }

        function editItem(id){
            var d=allData.find(function(x){return x.id===id;});
            if(!d) return;
            document.getElementById('mTitle').textContent='Редактировать';
            document.getElementById('f-id').value=id;
            document.getElementById('f-art').value=d.article||'';
            document.getElementById('f-name').value=d.name||'';
            document.getElementById('f-brand').value=d.brand||'';
            document.getElementById('f-cat').value=d.category||'';
            document.getElementById('f-type').value=d.item_type||'';
            document.getElementById('f-unit').value=d.unit||'шт';
            if(curType==='mat'){
                document.getElementById('f-purch').value=d.purchase_price||0;
                document.getElementById('f-retail').value=d.retail_price||0;
            } else {
                document.getElementById('f-wprice').value=d.price||0;
            }
            document.getElementById('pRowMat').style.display=curType==='mat'?'flex':'none';
            document.getElementById('pRowWork').style.display=curType==='work'?'block':'none';
            document.getElementById('editModal').classList.add('active');
        }

        async function saveItem(){
            var id=document.getElementById('f-id').value;
            var isEdit=!!id;
            var url=isEdit?'/estimate/api/catalog/'+(curType==='mat'?'materials':'works')+'/'+id:'/estimate/api/catalog/'+(curType==='mat'?'materials':'works');
            var m=isEdit?'PUT':'POST';
            var b={name:document.getElementById('f-name').value, unit:document.getElementById('f-unit').value, category:document.getElementById('f-cat').value, description:document.getElementById('f-type').value};
            if(curType==='mat'){
                b.article=document.getElementById('f-art').value;
                b.brand=document.getElementById('f-brand').value;
                b.purchase_price=parseFloat(document.getElementById('f-purch').value)||0;
                b.retail_price=parseFloat(document.getElementById('f-retail').value)||0;
                b.price=b.retail_price;
            } else {
                b.price=parseFloat(document.getElementById('f-wprice').value)||0;
            }
            try{
                var r=await fetch(url,{method:m,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:JSON.stringify(b)});
                if(!r.ok){var e=await r.json();alert('Ошибка: '+(e.error||''));return;}
            }catch(e){alert('Ошибка: '+e.message);return;}
            closeModal(); loadData();
        }

        async function delItem(id){
            if(!confirm('Удалить?')) return;
            await fetch('/estimate/api/catalog/'+(curType==='mat'?'materials':'works')+'/'+id,{method:'DELETE',headers:{'X-CSRF-Token':csrf()}});
            loadData();
        }

        function closeModal(){ document.getElementById('editModal').classList.remove('active'); }
        document.getElementById('editModal').addEventListener('click',function(e){ if(e.target.id==='editModal') closeModal(); });

        loadData();
    </script>
</body>
</html>'''

with open('templates/estimate/catalog.html', 'w', encoding='utf-8') as f:
    f.write(html_content)

print("✓ catalog.html обновлён!")
