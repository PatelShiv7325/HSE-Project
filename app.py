import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # Debug mode lets anyone who can reach the page run code on this PC, so it is OFF unless you
    # ask for it on purpose:   set FLASK_DEBUG=1   (Windows)   /   export FLASK_DEBUG=1   (Linux)
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
