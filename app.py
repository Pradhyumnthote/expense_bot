from flask import Flask, render_template_string
import mysql.connector

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'Jay@14111',
    'database': 'telegrambot'
}

app = Flask(__name__)

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<title>Your Expenses</title>
</head>
<body>
  <h2>Expenses for User: {{ username }}</h2>
  {% if expenses %}
      <table border="1">
          <tr>
              <th>Date</th>
              <th>ID</th>
              <th>Description</th>
              <th>Amount</th>
          </tr>
          {% for exp in expenses %}
          <tr>
              <td>{{ exp['date'] }}</td>
              <td>{{ exp['id'] }}</td>
              <td>{{ exp['description'] }}</td>
              <td>₹{{ exp['amount'] }}</td>
          </tr>
          {% endfor %}
      </table>
  {% else %}
      <p>No expenses found.</p>
  {% endif %}
</body>
</html>
"""

@app.route('/user/<username>')
def user_expenses(username):
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT date, id, description, amount FROM expenses WHERE username = %s ORDER BY date DESC", (username,))
    expenses = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template_string(TEMPLATE, username=username, expenses=expenses)

if __name__ == "__main__":
    app.run(debug=True, port=8080)
