# Immich dedup

Small script for duplicate photos in [Immich](https://immich.app). It reads Immich's duplicate list and either moves extra copies to trash, or stacks similar photos.

## Setup

```bash
export IMMICH_URL=https://photos.example.com
export IMMICH_API_KEY=your-key
```

Use the site URL without `/api`. The key needs access to duplicates. Trashing needs permission to delete assets. Stacking needs permission to create stacks.

## Run

```bash
python3 dedup.py
python3 dedup.py --apply
python3 dedup.py --scenario 4
```

With no `--scenario`, the script runs scenarios 1, 2, 3, and 4 in that order. It asks:

```text
Enable automatic grouping of photos if they are similar? [yes/N]
```

`yes` includes scenario 3. Any other answer skips scenario 3 and still runs 1, 2, and 4. A file is handled by the first matching scenario and is not offered again. `--scenario 1`, `2`, `3`, or `4` runs only that scenario.

Without `--apply` the script only prints a plan. With `--apply` it asks again before it changes anything.

## Scenarios

1. **Same name.** Same owner, and the file name matches after ignoring ` (1)`, ` (2)`, and letter case. If the capture time matches (a gap of a couple of seconds, or a timezone offset in 30-minute steps up to 14 hours), smaller files go to trash. If the time does not match, they are only listed.
2. **Same second.** Same owner and the same capture second, inside one duplicate group. Two files of the same type: the smaller one goes to trash. More than two files, or a photo mixed with a video: listed only.
3. **Photo series.** Inside one Immich duplicate group, photos from the same calendar day whose file sizes stay within 33% of each other. Immich has already decided the pictures look similar, so the file names can differ. `IMG_20220727_123236.jpg` and `IMG_20220727_123238.jpg` are stacked. `2025-02-17 23-36-49.JPG` and `2025-02-17 23-36-55.JPG` are stacked. Photos from another day in that group are left for their own stack. A 4 MB file and a 2 MB file are not stacked. The largest file stays on top. Nothing is deleted. Photos that are already in a stack are left alone.

4. **Compressed copy.** Inside one Immich duplicate group, photos with the same creation date. The largest file stays. A smaller file goes to trash when the largest is more than 33% bigger than it. The file names can differ. A 3.9 MB photo and a 365 KB photo from 2 Oct 2021: the 365 KB file is removed. A photo mixed with a video is only listed.

## License

[MIT](LICENSE). You can use, change, and share this code. Keep the copyright notice.
