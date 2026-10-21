"""
The site's writing: lab notes, projects and their categories. The admin's
routes and the public ones call these; so will anything else that edits
the content, which is why they raise db.rows' errors and never HTTP ones.
"""
