import uuid

import concurrent

lock_prefix = '/locks/'


class Lock(object):
    """
    A distributed lock.

    This can be used as a context manager, with the lock being acquired and
    released as you would expect:

    .. code-block:: python

        etcd = etcd3.client()

        # create a lock that expires after 20 seconds
        with etcd.lock('toot', ttl=20) as lock:
            # do something that requires the lock
            print(lock.is_acquired())

            # refresh the timeout on the lease
            lock.refresh()

    :param name: name of the lock
    :type name: string or bytes
    :param ttl: length of time for the lock to live for in seconds. The lock
                will be released after this time elapses, unless refreshed
    :type ttl: int
    """

    def __init__(self, name, ttl=60,
                 etcd_client=None):
        self.name = name
        self.ttl = ttl
        if etcd_client is not None:
            self.etcd_client = etcd_client

        self.key = lock_prefix + self.name
        self.lease = None
        # store uuid as bytes, since it avoids having to decode each time we
        # need to compare
        self.uuid = uuid.uuid1().bytes

    async def acquire(self, timeout=10):
        """Acquire the lock.

        :params timeout: Maximum time to wait before returning. `None` means
                         forever, any other value equal or greater than 0 is
                         the number of seconds.
        :returns: True if the lock has been acquired, False otherwise.

        """
        self.lease = await self.etcd_client.lease(self.ttl)

        success, _ = await self.etcd_client.transaction(
            compare=[
                self.etcd_client.transactions.create(self.key) == 0
            ],
            success=[
                self.etcd_client.transactions.put(self.key, self.uuid,
                                                  lease=self.lease)
            ],
            failure=[
                self.etcd_client.transactions.get(self.key)
            ]
        )
        if success is True:
            return True
        self.lease = None

        try:
            await self.etcd_client.watch_once(self.key, timeout)
        except concurrent.futures._base.TimeoutError:
            return False

        return True

    async def release(self):
        """Release the lock."""
        success, _ = await self.etcd_client.transaction(
            compare=[
                self.etcd_client.transactions.value(self.key) == self.uuid
            ],
            success=[self.etcd_client.transactions.delete(self.key)],
            failure=[]
        )
        return success

    async def refresh(self):
        """Refresh the time to live on this lock."""
        if self.lease is not None:
            return await self.lease.refresh()
        else:
            raise ValueError('No lease associated with this lock - have you '
                             'acquired the lock yet?')

    async def is_acquired(self):
        """Check if this lock is currently acquired."""
        uuid, _ = await self.etcd_client.get(self.key)

        if uuid is None:
            return False

        return uuid == self.uuid

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exception_type, exception_value, traceback):
        await self.release()
