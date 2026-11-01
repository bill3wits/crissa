"""House identity enrollment — family voice, people scaffold, office room refs.

Portable init for any office: read site.json, self-explaining audio cues,
writes under people/<id>/{face,voice}/ and site/reference-frames/.

  python -m crissa_presence.enroll_panel status
  python -m crissa_presence.enroll_panel voice <user_id>
  python -m crissa_presence.enroll_panel family
  python -m crissa_presence.enroll_panel room-refs
"""

__version__ = "1.0.0"
