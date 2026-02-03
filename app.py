from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import json

app = Flask(__name__)
# 設定 Session 加密用的密鑰
app.secret_key = 'lab_secret_key_123'
# 設定資料庫路徑 (使用 SQLite)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///lab_res.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- 資料庫模型 (Database Models) ---

class User(db.Model):
    """使用者清單"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

class FreezerBox(db.Model):
    """漸凍盒 (Cell Freezing Box) 管理模型"""
    id = db.Column(db.Integer, primary_key=True)
    box_name = db.Column(db.String(50))   # 盒子編號/名稱
    user_name = db.Column(db.String(50))  # 目前使用者
    start_date = db.Column(db.Date)       # 開始使用日期

class BSCReservation(db.Model):
    """生物安全櫃 (BSC) 預約模型"""
    id = db.Column(db.Integer, primary_key=True)
    bsc_id = db.Column(db.Integer)        # BSC 編號 (例如 1-4 號)
    date = db.Column(db.Date)             # 預約日期
    time_slot = db.Column(db.Integer)     # 時段編號
    user_name = db.Column(db.String(50))  # 預約人

class IHCReservation(db.Model):
    """免疫組織化學染色 (IHC) 預約模型"""
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date)             # 預約日期
    time_slot = db.Column(db.String(20))  # 時段 (AM1, PM1 等)
    trays = db.Column(db.Integer)         # 使用盤數
    user_name = db.Column(db.String(50))  # 預約人

# 啟動時自動建立資料庫檔案與資料表
with app.app_context():
    db.create_all()

# --- 全域變數注入 (Context Processor) ---
@app.context_processor
def inject_users():
    """讓所有 HTML 模板都能直接使用 users (選單) 和 current_user (登入狀態)"""
    return dict(users=User.query.all(), current_user=session.get('user'))

# --- 核心路由 (Core Routes) ---

@app.route('/')
def index():
    """首頁"""
    return render_template('index.html')

@app.route('/set_user', methods=['POST'])
def set_user():
    """切換目前登入的使用者 (存入 Session)"""
    session['user'] = request.form.get('user_name')
    # 返回前一頁或首頁
    return redirect(request.referrer or url_for('index'))

@app.route('/manage_users', methods=['GET', 'POST'])
def manage_users():
    """使用者清單管理：新增與刪除使用者"""
    if request.method == 'POST':
        # 處理新增使用者
        if 'add_user' in request.form:
            name = request.form.get('new_name', '').strip()
            # 限制名稱長度並檢查是否重複
            if name and len(name) <= 10:
                if not User.query.filter_by(name=name).first():
                    db.session.add(User(name=name))
                    db.session.commit()
        # 處理刪除使用者
        elif 'del_user' in request.form:
            user_id = request.form.get('user_id')
            user = User.query.get(user_id)
            if user:
                db.session.delete(user)
                db.session.commit()
    return render_template('manage_users.html')

# --- BSC 預約系統 ---

@app.route('/bsc')
def bsc():
    """顯示 BSC 預約表格 (呈現未來 14 天的狀態)"""
    today = datetime.now().date()
    # 產生未來 14 天的日期清單
    dates = [today + timedelta(days=i) for i in range(14)]
    # 撈取資料庫中所有相關預約 (包含昨天以後的資料以防跨日查看)
    all_res = BSCReservation.query.filter(BSCReservation.date >= (today - timedelta(days=1))).all()
    
    # 整理資料格式為 { bsc_id: { (date, slot): user_name } } 方便前端呈現
    booked = {i: {} for i in range(1, 5)}
    for r in all_res:
        if r.bsc_id in booked:
            booked[r.bsc_id][(r.date, r.time_slot)] = r.user_name
            
    return render_template('bsc.html', dates=dates, booked=booked, today=today, end_date=today + timedelta(days=13))

@app.route('/bsc_batch', methods=['POST'])
def bsc_batch():
    """處理前端傳回的批量預約或取消 (AJAX 異步處理)"""
    if not session.get('user'):
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json.get('items', [])
    current_user = session['user']
    
    for item in data:
        target_date = datetime.strptime(item['date'], '%Y-%m-%d').date()
        bsc_id, slot, mode = int(item['bsc']), int(item['slot']), item['mode']
        
        if mode == 'reserve':
            # 檢查該時段是否已被預約
            if not BSCReservation.query.filter_by(bsc_id=bsc_id, date=target_date, time_slot=slot).first():
                db.session.add(BSCReservation(bsc_id=bsc_id, date=target_date, time_slot=slot, user_name=current_user))
        elif mode == 'cancel':
            # 僅能取消自己的預約
            res = BSCReservation.query.filter_by(bsc_id=bsc_id, date=target_date, time_slot=slot, user_name=current_user).first()
            if res:
                db.session.delete(res)
    
    db.session.commit()
    return jsonify({"status": "success"})

# --- Freezer 漸凍盒系統 ---

@app.route('/freezer', methods=['GET', 'POST'])
def freezer():
    """管理漸凍盒：包含使用、歸還、新增盒號與逾期計算"""
    today = datetime.now().date()
    
    if request.method == 'POST':
        # 批量新增盒號 (支援逗號分隔)
        if 'add_box' in request.form:
            raw_input = request.form.get('box_name', '')
            names = [n.strip() for n in raw_input.replace('，', ',').split(',') if n.strip()]
            for name in names:
                if not FreezerBox.query.filter_by(box_name=name).first():
                    db.session.add(FreezerBox(box_name=name))
            db.session.commit()
            
        # 登記開始使用
        elif 'use_box' in request.form and session.get('user'):
            box = FreezerBox.query.get(request.form.get('box_id'))
            if box:
                box.user_name = session['user']
                box.start_date = today
                db.session.commit()
                
        # 歸還盒子 (清空使用者與日期)
        elif 'return_box' in request.form:
            box = FreezerBox.query.get(request.form.get('box_id'))
            if box:
                box.user_name = box.start_date = None
                db.session.commit()
                
        # 刪除盒號 (僅限目前沒人在用的盒子)
        elif 'delete_box' in request.form:
            box = FreezerBox.query.get(request.form.get('box_id'))
            if box and not box.user_name:
                db.session.delete(box)
                db.session.commit()
                
        return redirect(url_for('freezer'))

    # 處理天數計算、逾期邏輯與前端排序
    all_boxes = FreezerBox.query.all()
    in_use, available = [], []
    
    for b in all_boxes:
        if b.user_name:
            # 計算已使用天數
            b.days_used = (today - b.start_date).days
            # 超過 7 天視為逾期
            b.overdue_days = max(0, b.days_used - 7)
            # 排序邏輯：逾期 7 天以上者權重提高，優先排在清單前方
            b.priority = -b.days_used if b.days_used >= 7 else 0
            in_use.append(b)
        else:
            available.append(b)

    # 排序：使用中的按權重排序，空閒的按盒號排序
    in_use.sort(key=lambda x: (x.priority, x.box_name))
    available.sort(key=lambda x: x.box_name)

    return render_template('freezer.html', in_use_boxes=in_use, available_boxes=available, today=today)

# --- IHC 染色系統 ---

@app.route('/ihc')
def ihc():
    """顯示 IHC 預約頁面"""
    today = datetime.now().date()
    # 產生未來 14 天日期
    dates = [today + timedelta(days=i) for i in range(14)]
    
    # 定義時段 (對應前端的 3 盤系統)
    slots = ['AM1', 'AM2', 'AM3', 'PM1', 'PM2', 'PM3']
    
    # 撈取預約資料並整理成前端需要的格式: { "2023-10-01": { "AM1": "UserA" } }
    all_res = IHCReservation.query.filter(IHCReservation.date >= today).all()
    booked_data = {}
    for r in all_res:
        d_str = r.date.strftime('%Y-%m-%d')
        if d_str not in booked_data:
            booked_data[d_str] = {}
        booked_data[d_str][r.time_slot] = r.user_name
            
    return render_template('ihc.html', dates=dates, slots=slots, booked_data=booked_data)

@app.route('/ihc_batch', methods=['POST'])
def ihc_batch():
    """處理 IHC 批量預約/取消"""
    if not session.get('user'):
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    action = data.get('action')
    target_date = datetime.strptime(data.get('date'), '%Y-%m-%d').date()
    slot = data.get('slot')
    current_user = session['user']

    if action == 'book':
        # 檢查是否已被預約
        exists = IHCReservation.query.filter_by(date=target_date, time_slot=slot).first()
        if not exists:
            # 這裡預設 trays 為 1，因為前端是一格一格選
            db.session.add(IHCReservation(date=target_date, time_slot=slot, trays=1, user_name=current_user))
    
    elif action == 'cancel':
        # 只能取消自己的
        res = IHCReservation.query.filter_by(date=target_date, time_slot=slot, user_name=current_user).first()
        if res:
            db.session.delete(res)
            
    db.session.commit()
    return jsonify({"status": "success"})

if __name__ == '__main__':
    # 以偵錯模式啟動伺服器
    app.run(debug=True)
