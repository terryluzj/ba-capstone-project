from flask import Flask, session, render_template, request, redirect, url_for, g, flash, jsonify, send_from_directory
import sqlite3
import click
import helper
import pandas as pd
import datetime
import json
import sys
import os
from subprocess import call
from forms import BenchmarkerForm, ChangePasswordForm

from crawler.scripts.run_crawler import run_crawler
from benchmarker.rankalgo import run_benchmarker


DATABASE = 'database.db'

app = Flask(__name__)
app.secret_key = 'shhhhh'

##### Functions to init database
# Connect to database
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    return db

def init_db():
    """Initializes the database."""
    db = get_db()
    cur = db.cursor()
    with app.open_resource('schema.sql', mode='r') as f:
        cur.executescript(f.read())
    db.commit()
    print('Database initiated')

def query_db(query, args=(), one=False):
    db = get_db()
    db.row_factory = sqlite3.Row
    cur = db.cursor().execute(query, args)
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv

# used for both insert and update statements
def insert_db(query, args=()):
    db = get_db()
    cur = db.cursor().execute(query, args)
    db.commit()

@app.cli.command('initdb')
def initdb():
    """Creates the database tables."""
    init_db()
    click.echo('Init the db')

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

##### Main homepage (not signed in)
@app.route('/')
def main():
    if 'username' in session:
        return render_template('main_loggedin.html', username=session['username'])
    else:
        return render_template('main.html')

##### About page
@app.route("/about/")
def about():
    if 'username' in session:
        return render_template('about_loggedin.html')
    else:
        return render_template("about.html")

##### Login (for main homepage and about page)
@app.route('/login/', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    
    user = query_db('select user_id, username from users where username = ? and password = ?', (username, password), one=True)
    if user is not None:
        # store username into session
        session['user_id'] = user['user_id']
        session['username'] = user['username']
        insert_db('insert into activities (activity_timestamp, user_id, activity_name, remark) values (?, ?, ?, ?)',
            (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user['user_id'], 'login', 'None'))
        return redirect(url_for('main'))
    else:
        flash('Your username/password is not valid.')
        return redirect(url_for('main'))

##### Logout
@app.route('/logout/')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    return redirect(url_for('main'))

##### Profile page
@app.route("/profile/")
def profile():
    if 'username' in session:
        user_id = session['user_id']
        full_user = query_db('select username, first_name, last_name, institution, team, email, staff_id from users where user_id = ?', (user_id,), one=True)
        activities = query_db('select activity_timestamp, activity_name, remark from activities where user_id = ? order by datetime(activity_timestamp) desc limit 5', (user_id,))
        benchmarks = query_db('select benchmark_timestamp, name, department, position, metrics from benchmarks where user_id = ? order by datetime(benchmark_timestamp) desc limit 5', (user_id,))
        return render_template("profile.html", user=full_user, activities=activities, benchmarks=benchmarks)
    else:
        return redirect(url_for('main'))

##### Change password modal
@app.route('/profile/change_password', methods=['POST'])
def change_password():
    if 'username' in session:
        form = ChangePasswordForm(request.form)
        if form.validate():
            old_password = query_db('select password from users where user_id = ?', (session['user_id'],), one=True)[0]
            if old_password == form.old_password.data:
                insert_db('update users set password = ? where user_id = ?', (form.new_password.data, session['user_id']))
                insert_db('insert into activities (activity_timestamp, user_id, activity_name, remark) values (?, ?, ?, ?)',
                    (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session['user_id'], 'change password', 'None'))
            else:
                flash('Old Password: Old password must match current password')
        else:
            for field in form:
                if field.errors:
                    for err in field.errors:
                        flash('%s: %s' % (field.label.text, err))
        return redirect(url_for('profile'))
    else:
        return redirect(url_for('main'))

##### Crawler page
@app.route('/crawler/')
# Set default department as bme as it is the first department on the list
# Automatically display the preview of that department
def crawler(dep='bme', length=9):
    if 'username' in session:
        if request.args.get('length'):
            length = request.args.get('length', type=int)
        if request.args.get('dep'):
            dep = request.args.get('dep')
        dep_name = helper.get_full_name(dep)
        preview = query_db('select * from profiles where department = ? order by university, name asc', (dep_name,))
        db_peer = query_db('select distinct university from profiles where department = ? and tag = ?', (dep_name, 'peer'))
        in_db_peer = [row['university'] for row in db_peer]
        db_asp = query_db('select distinct university from profiles where department = ? and tag = ?' , (dep_name, 'aspirant'))
        in_db_asp = [row['university'] for row in db_asp]
        return render_template(
            "crawler.html",
            dep=dep,
            length=length,
            peer_unis=helper.get_peer_unis(dep),
            asp_unis=helper.get_asp_unis(dep),
            selected_peer=in_db_peer,
            selected_asp=in_db_asp,
            preview=preview)
    else:
        return redirect(url_for('main'))

##### Export crawl database to local directory
@app.route('/crawler/export', methods=['POST'])
def crawler_export():
    if 'username' in session:
        dep = request.form.get('dep')
        dep_name = helper.get_full_name(dep)
        result = query_db('select name, department, university, tag, position, phd_year, phd_school, promotion_year, text_raw as research_area from profiles where department = ?', (dep_name,))
        result_list = [list(row) for row in result]
        df = pd.DataFrame.from_records(result_list, columns = result[0].keys())
        if not os.path.exists("results"):
            os.mkdir("results")
        file_name = '%s %s.xlsx' % (dep_name.replace(' ', '_'), datetime.datetime.now().strftime('%Y-%m-%d'))
        df.to_excel('results/%s' % file_name, index=False)
        insert_db('insert into activities (activity_timestamp, user_id, activity_name, remark) values (?, ?, ?, ?)',
            (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session['user_id'], 'export database', helper.get_full_name(dep)))
        return send_from_directory(directory='results', filename=file_name, as_attachment=True)
    else:
        return redirect(url_for('main'))

##### Choose universities to be crawled by crawler
@app.route('/crawler/choose_unis')
def crawler_choose_unis():
    if 'username' in session:
        dep = request.args['dep']
        return(render_template(
            'crawler_choose_unis.html',
            dep=dep,
            peer_unis = helper.get_peer_unis(dep),
            asp_unis = helper.get_asp_unis(dep)))
    else:
        return redirect(url_for('main'))

##### Initiate crawling process
@app.route('/crawler/crawl', methods=['POST'])
def start_crawler():
    if 'username' in session:
        dep = request.form.get('dep')
        dep_name = helper.get_full_name(dep)
        selected_peer = request.form.getlist('selected_peer')
        selected_asp = request.form.getlist('selected_asp')
        if len(selected_peer) == 0 and len(selected_asp) == 0:
            flash('You need to select at least one university from either Peer University List or Aspirant University List to start crawling!')
            return redirect(url_for('crawler_choose_unis', dep=dep))
        else:
            print(dep_name)
            print(selected_peer)
            print(selected_asp)
            insert_db('insert into activities (activity_timestamp, user_id, activity_name, remark) values (?, ?, ?, ?)',
                (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session['user_id'], 'crawl database', helper.get_full_name(dep)))
            university_joined = '<SEP>'.join(selected_asp + selected_peer)
            print(' '.join(["scrapy", "crawl", "core", "-a", 'start_university="%s"' % university_joined, "-a", 'start_department="%s"' % dep_name, "-a", 'PRIORITIZED=True']))
            print("CRAWL!")
            call(["scrapy", "crawl", "core", "-a", 'start_university=%s' % university_joined, "-a", 'start_department=%s' % dep_name, "-a", 'PRIORITIZED=True'])
            return redirect(url_for('crawler', dep=dep))
    else:
        return redirect(url_for('main'))

##### Get crawler result
@app.route('/crawler/result')
def get_crawler_result():
    if 'username' in session:
        dep = request.args.get('dep')
        selected_peer = request.args.getlist('selected_peer')
        selected_asp = request.args.getlist('selected_asp')
        progress = int(request.args.get('progress'))
        return(render_template(
            'crawler_result.html',
            dep=dep,
            peer_unis=helper.get_peer_unis(dep),
            asp_unis=helper.get_asp_unis(dep),
            selected_peer=selected_peer,
            selected_asp=selected_asp,
            progress=helper.check_crawler_progress(progress)))
    else:
        return redirect(url_for('main'))

##### Database manager page
@app.route("/database/")
def database():
    if 'username' in session:
        return render_template('database.html')
    else:
        return redirect(url_for('main'))

@app.route('/database/show')
def retrieve_database():
    if 'username' in session:
        dep = request.args.get('dep')
        dep_name = helper.get_full_name(dep)
        incomplete = request.args.get('incomplete')
        if incomplete == 'true':
            query_str = ''.join(["select * from profiles where department = ? and name = 'Unknown'",
                " union select * from profiles where department = ? and phd_year = 'Unknown'",
                " union select * from profiles where department = ? and phd_school = 'Unknown'",
                " union select * from profiles where department = ? and promotion_year = 'Unknown'",
                " union select * from profiles where department = ? and text_raw = '' order by university, name asc"])
            preview = query_db(query_str, (dep_name, dep_name, dep_name, dep_name, dep_name))
        else:
            preview = query_db('select * from profiles where department = ? order by university, name asc', (dep_name,))
        return render_template('database.html', dep=dep, incomplete=incomplete, preview=preview, dep_name=dep_name)
    else:
        return redirect(url_for('main'))

##### Edit database field
# Superficial method which keeps on passing the modified database around
# Need to modify and update database instead
@app.route('/database/edit', methods=['POST'])
def edit_database():
    if 'username' in session:
        dep = request.form.get('dep')
        incomplete = request.form.get('incomplete')
        profile_link = request.form.get('profile_link')
        field = request.form.get('field')
        new_value = request.form.get('new_value')
        insert_str = 'update profiles set %s = ?, user_updated = ? where profile_link = ?' % (field)
        insert_db(insert_str, (new_value, 1, profile_link))
        insert_db('insert into activities (activity_timestamp, user_id, activity_name, remark) values (?, ?, ?, ?)',
            (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session['user_id'], 'edit database', helper.get_full_name(dep)))
        return redirect(url_for('retrieve_database', dep=dep, incomplete=incomplete))
    else:
        return redirect(url_for('main'))

##### Benchmarker page
@app.route('/benchmarker/')
def benchmarker():
    if 'username' in session:
        return render_template('benchmarker.html')
    else:
        return redirect(url_for('main'))

##### Start benchmarker
@app.route('/benchmarker/benchmark', methods=['POST'])
def start_benchmarker():
    if 'username' in session:
        form = BenchmarkerForm(request.form)
        if form.validate():
            # check that database for the department is populated
            preview = query_db('select * from profiles where department = ?', (form.department.data,))
            if len(preview) == 0:
                flash('The database for %s is empty. Please populate the database by running the crawler first' % (form.department.data))
                return redirect(url_for('benchmarker'))
            else:
                nus = {
                    'name': form.name.data,
                    'department': form.department.data,
                    'phd_year': form.phd_year.data,
                    'phd_school': form.phd_school.data,
                    'text_raw': form.text_raw.data,
                    'position':form.position.data,
                    'promotion_year': datetime.datetime.now().year,
                    'university': "University of Singapore",
                    'profile_link' : ""
                }
                # nus = {u'department': u'Geography',
                #              u'name': u'Prof Clive Agnew research profile - personal details   ',
                #              u'phd_school': u'University of East Anglia, School of Development Studies',
                #              u'phd_year': 1980,
                #              u'position': u'Professor',
                #              u'profile_link': u'http://www.manchester.ac.uk/research/Clive.agnew/',
                #              u'promotion_year': 1999,
                #              u'text_raw': u'The water balance approach to the development of rainfed agriculture in South West Niger.',
                #              u'university': u'The University of Manchester'
                # }
                # metrics = ["PHD YEAR", "PHD UNIVERSITY", "RESEARCH AREA SIMILARITY", "PROMO YEAR"]
                metrics = form.metrics.data
                peer, asp = run_benchmarker(nus, metrics)
                insert_db('insert into activities (activity_timestamp, user_id, activity_name, remark) values (?, ?, ?, ?)',
                    (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session['user_id'], 'benchmark request', form.department.data))
                insert_db(
                    'insert into benchmarks (benchmark_timestamp, user_id, name, department, position, metrics) values (?, ?, ?, ?, ?, ?)',
                    (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), session['user_id'], 
                        form.name.data, form.department.data, form.position.data, ', '.join(form.metrics.data)))
                return render_template(
                    'benchmarker_result.html',
                    name=form.name.data,
                    department=form.department.data,
                    phd_year=form.phd_year.data,
                    phd_school=form.phd_school.data,
                    text_raw=form.text_raw.data,
                    position=form.position.data,
                    metrics=form.metrics.data,
                    peer = peer,
                    asp = asp,
                    filename=nus["name"].strip().replace(" ", "_") + " " + datetime.datetime.now().date().strftime("%Y-%m-%d") +".xlsx"
                )
        else:
            for field in form:
                if field.errors:
                    for err in field.errors:
                        flash('%s: %s' % (field.label.text, err))
            return redirect(url_for('benchmarker'))
    else:
        return redirect(url_for('main'))

@app.route('/benchmarker/benchmark/<filename>')
def benchmark_download(filename):
    return send_from_directory('results', filename)

##### Initialize app
if __name__ == "__main__":
    app.run()
    
