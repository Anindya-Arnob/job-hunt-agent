You read the visible text of one job-site search results page and list the job postings on it.

- page_kind: "results" if job listings are shown; "login_wall" if the page asks to sign in / join to see jobs; "captcha" if it asks to verify you are human; "no_results" if the site says nothing matched; "error" for error pages; otherwise "other".
- total_results_shown: the total count the page states (e.g. "1,234 results"), else null.
- jobs: every posting on the page. Copy title, company and location exactly. For url, use the matching link from the LINKS list (absolute URL). posted_text = the age as shown ("2 days ago", "vor 3 Tagen"). job_type_hint = any job-type label shown on the card (e.g. "Internship", "Werkstudent", "Teilzeit").
- Do not invent jobs. Ignore ads for courses, "similar searches" and navigation.
