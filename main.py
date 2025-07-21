
from flask import Flask, render_template, request, session, redirect, url_for
from flask_socketio import join_room, leave_room, send, SocketIO, emit
import random
from string import ascii_uppercase
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = "hjhjsdahhds"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///chat_users.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)
socketio = SocketIO(app)
# Direct chat page
@app.route("/chat/<username>")
def chat(username):
    if 'name' not in session or not session['name']:
        return redirect(url_for('login'))
    if session['name'] == username:
        return redirect(url_for('home'))
    # Fetch chat history between logged-in user and selected user
    messages = Message.query.filter(
        ((Message.sender == session['name']) & (Message.recipient == username)) |
        ((Message.sender == username) & (Message.recipient == session['name']))
    ).order_by(Message.timestamp).all()
    return render_template("chat.html", other_user=username, messages=messages)

# Logout route
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


rooms = {}
# Track online users: username -> sid
online_users = {}


# User model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"

# Message model
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(80), nullable=False)
    recipient = db.Column(db.String(80), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=func.now())


with app.app_context():
    db.create_all()
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if not username or not password:
            return render_template("register.html", error="Please provide username and password.")
        if User.query.filter_by(username=username).first():
            return render_template("register.html", error="Username already exists.")
        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session["name"] = username
            return redirect(url_for("home"))
        else:
            return render_template("login.html", error="Invalid username or password.")
    return render_template("login.html")

def generate_unique_code(length):
    while True:
        code = ""
        for _ in range(length):
            code += random.choice(ascii_uppercase)
        
        if code not in rooms:
            break
    
    return code

@app.route("/", methods=["POST", "GET"])
def home():
    if request.method == "POST":
        name = request.form.get("name")
        code = request.form.get("code")
        join = request.form.get("join", False)
        create = request.form.get("create", False)

        if not name:
            users = User.query.all()
            user_list = [u.username for u in users]
            return render_template("home.html", error="Please enter a name.", code=code, name=name, users=user_list)

        if join != False and not code:
            users = User.query.all()
            user_list = [u.username for u in users]
            return render_template("home.html", error="Please enter a room code.", code=code, name=name, users=user_list)
        
        room = code
        if create != False:
            room = generate_unique_code(4)
            rooms[room] = {"members": 0, "messages": []}
        elif code not in rooms:
            users = User.query.all()
            user_list = [u.username for u in users]
            return render_template("home.html", error="Room does not exist.", code=code, name=name, users=user_list)
        
        session["room"] = room
        session["name"] = name
        return redirect(url_for("room"))

    users = User.query.all()
    user_list = [u.username for u in users]
    return render_template("home.html", users=user_list)


@app.route("/room")
def room():
    room = session.get("room")
    if room is None or session.get("name") is None or room not in rooms:
        return redirect(url_for("home"))
    # Show online users
    users = list(online_users.keys())
    return render_template("room.html", code=room, messages=rooms[room]["messages"], users=users)

# API endpoint to get all users (registered)

@app.route("/users")
def get_users():
    current_user = session.get('name')
    users = User.query.all()
    filtered_users = [u.username for u in users if u.username != current_user]
    return {"users": filtered_users}

# API endpoint to get chat history between two users
@app.route("/messages/<user1>/<user2>")
def get_messages(user1, user2):
    messages = Message.query.filter(
        ((Message.sender == user1) & (Message.recipient == user2)) |
        ((Message.sender == user2) & (Message.recipient == user1))
    ).order_by(Message.timestamp).all()
    return {"messages": [
        {"sender": m.sender, "recipient": m.recipient, "content": m.content, "timestamp": m.timestamp.strftime('%Y-%m-%d %H:%M:%S')} for m in messages
    ]}

# API endpoint to get online users
@app.route("/online_users")
def get_online_users():
    return {"online_users": list(online_users.keys())}



# Direct message event (store in DB)
@socketio.on("direct_message")
def direct_message(data):
    sender = session.get("name")
    recipient = data.get("to")
    msg = data.get("message")
    if not sender or not recipient or not msg:
        return
    # Store message in DB
    db.session.add(Message(sender=sender, recipient=recipient, content=msg))
    db.session.commit()
    sid = online_users.get(recipient)
    if sid:
        emit("direct_message", {"from": sender, "message": msg}, room=sid)
        print(f"{sender} sent direct message to {recipient}: {msg}")
    else:
        emit("direct_message", {"from": "system", "message": f"User {recipient} is not online."}, room=online_users.get(sender))

@socketio.on("message")
def message(data):
    room = session.get("room")
    if room not in rooms:
        return 
    content = {
        "name": session.get("name"),
        "message": data["data"]
    }
    send(content, to=room)
    rooms[room]["messages"].append(content)
    print(f"{session.get('name')} said: {data['data']}")


@socketio.on("connect")
def connect(auth):
    room = session.get("room")
    name = session.get("name")
    if not room or not name:
        return
    if room not in rooms:
        leave_room(room)
        return
    # Register user in DB if not exists
    if not User.query.filter_by(username=name).first():
        db.session.add(User(username=name))
        db.session.commit()
    # Track online user
    online_users[name] = request.sid
    join_room(room)
    send({"name": name, "message": "has entered the room"}, to=room)
    rooms[room]["members"] += 1
    print(f"{name} joined room {room}")


@socketio.on("disconnect")
def disconnect():
    room = session.get("room")
    name = session.get("name")
    leave_room(room)
    # Remove from online users
    if name in online_users:
        del online_users[name]
    if room in rooms:
        rooms[room]["members"] -= 1
        if rooms[room]["members"] <= 0:
            del rooms[room]
    send({"name": name, "message": "has left the room"}, to=room)
    print(f"{name} has left the room {room}")

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
