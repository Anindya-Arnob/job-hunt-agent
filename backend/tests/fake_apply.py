"""A fake job posting and company application form (like Greenhouse / Personio) for the apply tests."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

POSTING = """<html><body><h1>Working Student Data Science (m/w/d)</h1><p>Acme Analytics GmbH, Berlin</p>
<p>Python, SQL, Spark nice to have. 20h/week.</p>
<a href="/ats/acme/apply">Apply on company website</a> <a href="/about">About us</a></body></html>"""

FORM = """<html><body><h2>Apply: Working Student Data Science</h2>
<form method="post" action="/ats/acme/submit" enctype="multipart/form-data">
 <label for="fn">First name *</label><input id="fn" name="first_name" required>
 <label for="ln">Last name *</label><input id="ln" name="last_name" required>
 <label for="em">Email *</label><input id="em" name="email" type="email" required>
 <label for="ph">Phone</label><input id="ph" name="phone" type="tel">
 <label for="li">LinkedIn profile</label><input id="li" name="linkedin" type="url">
 <label for="cv">Resume / CV *</label><input id="cv" name="resume" type="file" required>
 <label for="cl">Cover letter</label><input id="cl" name="cover" type="file">
 <label for="start">Earliest start date</label><input id="start" name="start">
 <label for="sal">Expected salary (€ per hour)</label><input id="sal" name="salary">
 <fieldset><legend>Are you authorized to work in Germany?</legend>
   <label><input type="radio" name="auth" value="yes">Yes</label><label><input type="radio" name="auth" value="no">No</label></fieldset>
 <label for="de">German language level</label>
 <select id="de" name="german"><option>Please select</option><option>A1</option><option>A2</option><option>B1</option>
   <option>B2</option><option>C1</option><option>C2 / Native</option></select>
 <fieldset><legend>Do you have experience with Python?</legend>
   <label><input type="radio" name="py" value="yes">Yes</label><label><input type="radio" name="py" value="no">No</label></fieldset>
 <fieldset><legend>Do you have experience with Spark?</legend>
   <label><input type="radio" name="spark" value="yes">Yes</label><label><input type="radio" name="spark" value="no">No</label></fieldset>
 <label for="why">Why do you want to work at Acme Analytics?</label><textarea id="why" name="why"></textarea>
 <label for="team">Which team at Acme would you most like to join and why?</label><input id="team" name="team">
 <label for="heard">How did you hear about us?</label>
 <select id="heard" name="heard"><option>Select</option><option>LinkedIn</option><option>Job board</option><option>Friend</option></select>
 <label for="g">Gender</label><select id="g" name="gender"><option>Select</option><option>Female</option><option>Male</option>
   <option>Diverse</option><option>Prefer not to say</option></select>
 <label><input type="checkbox" name="privacy" required> I agree to the privacy policy (Datenschutz)</label>
 <button type="submit">Submit application</button>
</form></body></html>"""

LONG = """<html><head><title>Acme Analytics - Careers</title></head><body><h2>Your application</h2>
<label for="lfn">First name</label><input id="lfn" name="first_name">
<div style="height:1600px"></div>
<label for="mot">Motivation</label><textarea id="mot" name="motivation" maxlength="300"></textarea>
<div>0 / 300</div><div style="height:1600px"></div>
<label for="lem">Email</label><input id="lem" name="email" type="email">
<button type="submit">Submit</button></body></html>"""

INFO = ("<html><body><h1>Data Science Intern</h1><h2>How we hire</h2><p>"
        + "We build analytics products for logistics customers across Europe. " * 45
        + "</p><p>Once your application has been received, we will reply within two weeks.</p>"
        "<a href='/ats/acme/apply'>Apply now</a></body></html>")

THANKS = "<html><body><h1>Thank you for your application!</h1><p>We have received your application.</p></body></html>"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, status=200):
        b = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/board/job/1"):
            return self._send(POSTING)
        if self.path.startswith("/ats/acme/info"):
            return self._send(INFO)
        if self.path.startswith("/ats/acme/thanks"):
            return self._send(THANKS)
        if self.path.startswith("/ats/acme/long"):
            return self._send(LONG)
        if self.path.startswith("/ats/acme/apply"):
            return self._send(FORM)
        return self._send("<html><body>not found</body></html>", 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(n)
        self.server.submissions.append(body)
        return self._send(THANKS)


class FakeApply:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.submissions = []
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    @property
    def submissions(self):
        return self.server.submissions
