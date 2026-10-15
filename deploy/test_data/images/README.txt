# place the 25 jpg files here before running the seed

expected filenames (see ../listings.json → photos[]):

01_a.jpg  01_b.jpg
02_a.jpg  02_b.jpg  02_c.jpg
03_a.jpg  03_b.jpg  03_c.jpg
04_a.jpg
05_a.jpg  05_b.jpg
06_a.jpg  06_b.jpg
07_a.jpg  07_b.jpg
08_a.jpg  08_b.jpg  08_c.jpg
09_a.jpg  09_b.jpg
10_a.jpg  10_b.jpg  10_c.jpg

if a file is missing, seed_test_data.py draws a coloured placeholder with the
district + price text — the seed never fails, so you can test the pipeline first
and only bother with real photos later.

free-to-use sources for apartment interiors (all CC0 / no attribution required):
  https://www.pexels.com/search/one%20room%20apartment%20interior/
  https://unsplash.com/s/photos/small-apartment-interior
  https://pixabay.com/images/search/apartment%20studio/
  https://commons.wikimedia.org/wiki/Category:Interiors_of_apartments

rename each downloaded file to the corresponding NN_x.jpg and drop it here.
