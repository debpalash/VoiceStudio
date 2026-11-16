# Saved dubbing projects

The Dubbing sidebar Projects tab lists saved projects with a visible trash
button. Deletion asks for confirmation and initially focuses Cancel. It waits
while a dubbing/rendering task or project load is active and prevents duplicate
submissions. A failed request retains the dialog for retry.

Deletion uses the existing project endpoint: only the saved database record is
removed. Media, voice profiles and rendered audio remain. If the deleted record
was open, its working draft stays available without the deleted saved ID.
The general Projects library retains its existing deletion controls.
