import asyncio
import os

from uldlib.segfile import SegFile
from ulslib import const


class AsyncSegFileReader(SegFile):
    """Implementation asynchronous segment read file"""

    async def read(self):
        last_pos = self.pfrom
        self.fp.seek(self.pfrom, os.SEEK_SET)

        while last_pos < self.pto:
            to_read = self.cur_pos - last_pos

            while to_read > 0:
                if const.OUTFILE_READ_BUF < to_read:
                    cur_read = const.OUTFILE_READ_BUF
                else:
                    cur_read = to_read

                yield self.fp.read(cur_read)
                to_read = to_read - cur_read

            last_pos = self.cur_pos
            await asyncio.sleep(0.1)
            self._read_stat()
