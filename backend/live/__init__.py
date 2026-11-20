"""The live side of an interview: the event stream, its store and its relay.

Track I (docs/TRACK_I.md, phase 2). `emit()` is how anything inside an
interview reports what happened; the store keeps it; the relay streams it to a
viewer. See track_i_notes/C_architecture.md for why events cross from the bot's
process to the web process through the database.
"""
