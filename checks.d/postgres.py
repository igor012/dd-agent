from checks import AgentCheck, CheckException

class PostgreSql(AgentCheck):
    """
    """

    RATE = AgentCheck.rate
    GAUGE = AgentCheck.gauge
    
    # turning columns into tags
    DB_METRICS = {
        'descriptors': [
            ('datname', 'db')
        ],
        'metrics': {
            'numbackends'       : ('postgresql.connections', GAUGE),
            'xact_commit'       : ('postgresql.commits', RATE),
            'xact_rollback'     : ('postgresql.rollbacks', RATE),
            'blks_read'         : ('postgresql.disk_read', RATE),
            'blks_hit'          : ('postgresql.buffer_hit', RATE),
            'tup_returned'      : ('postgresql.rows_returned', RATE),
            'tup_fetched'       : ('postgresql.rows_fetched', RATE),
            'tup_inserted'      : ('postgresql.rows_inserted', RATE),
            'tup_updated'       : ('postgresql.rows_updated', RATE),
            'tup_deleted'       : ('postgresql.rows_deleted', RATE),
        },
        'query': """
SELECT datname,
       %s
  FROM pg_stat_database
 WHERE datname not ilike 'template%%'
   AND datname not ilike 'postgres'
""",
        'relation': False,
    }

    NEWER_92_METRICS = {
        'deadlocks'         : ('postgresql.deadlocks', GAUGE),
        'temp_bytes'        : ('postgresql.temp_bytes', RATE),
        'temp_files'        : ('postgresql.temp_files', RATE),
    }

    REL_METRICS = {
        'descriptors': [
            ('relname', 'table')
        ],
        'metrics': {
            'seq_scan'          : ('postgresql.seq_scans', RATE),
            'seq_tup_read'      : ('postgresql.seq_rows_read', RATE),
            'idx_scan'          : ('postgresql.index_scans', RATE),
            'idx_tup_fetch'     : ('postgresql.index_rows_fetched', RATE),
            'n_tup_ins'         : ('postgresql.rows_inserted', RATE),
            'n_tup_upd'         : ('postgresql.rows_updated', RATE),
            'n_tup_del'         : ('postgresql.rows_deleted', RATE),
            'n_tup_hot_upd'     : ('postgresql.rows_hot_updated', RATE),
            'n_live_tup'        : ('postgresql.live_rows', GAUGE),
            'n_dead_tup'        : ('postgresql.dead_rows', GAUGE),
        },
        'query': """
SELECT relname,
       %s
  FROM pg_stat_user_tables
 WHERE relname = %s""",
        'relation': True,
    }

    IDX_METRICS = {
        'descriptors': [
            ('relname', 'table'),
            ('indexrelname', 'index')
        ],
        'metrics': {
            'idx_scan'          : ('postgresql.index_scans', RATE),
            'idx_tup_read'      : ('postgresql.index_rows_read', RATE),
            'idx_tup_fetch'     : ('postgresql.index_rows_fetched', RATE),
        },
        'query': """
SELECT relname,
       indexrelname,
       %s
  FROM pg_stat_user_indexes
 WHERE relname = %s""",
        'relation': True,
    }


    def __init__(self, name, init_config, agentConfig):
        AgentCheck.__init__(self, name, init_config, agentConfig)
        self.dbs = {}
        self.versions = {}

    def get_library_versions(self):
        try:
            import psycopg2
            version = psycopg2.__version__
        except ImportError:
            version = "Not Found"
        except AttributeError:
            version = "Unknown"

        return {"psycopg2": version}

    def _get_version(self, key, db):
        if key not in self.versions:
            cursor = db.cursor()
            cursor.execute('SHOW SERVER_VERSION;')
            result = cursor.fetchone()
            try:
                version = map(int, result[0].split('.'))
            except Exception:
                version = result[0]
            self.versions[key] = version

        return self.versions[key]

    def _is_9_2_or_above(self, key, db):
        version = self._get_version(key, db)
        if type(version) == list:
            return version >= [9,2,0]

        return False

    def _collect_stats(self, key, db, instance_tags, relations):
        """Query pg_stat_* for various metrics
        If relations is not an empty list, gather per-relation metrics
        on top of that.
        """

        # Clean up initial args
        if instance_tags is None:
            instance_tags = []

        # Extended 9.2+ metrics
        if self._is_9_2_or_above(key, db):
            self.DB_METRICS['metrics'].update(self.NEWER_92_METRICS)
  
        cursor = db.cursor()
        try:
            for scope in (self.DB_METRICS, self.REL_METRICS, self.IDX_METRICS):
                # build query
                cols = scope['metrics'].keys()  # list of metrics to query, in some order
                                                # we must remember that order to parse results
                query = scope['query'] % (", ".join(cols))  # assembled query

                # execute query
                cursor.execute(query)
                results = cursor.fetchall()

                # parse results
                # A row should look like this
                # (descriptor, descriptor, ..., value, value, value, value, ...)
                # with descriptor a PG relation or index name, which we use to create the tags
                
                for row in results:
                    desc = scope['descriptors']
                    # turn descriptors into tags
                    tags = instance_tags.extend(["%s:%s" % (d[0][1], d) for d in zip(desc, row[:len(desc)])])
                    print tags

                    # [(metric-map, value), (metric-map, value), ...]
                    # metric-map is: (dd_name, "rate"|"gauge")
                    # shift the results since the first columns will be the "descriptors"
                    values = zip([scope['metrics'][c] for c in cols], row[len(desc):])
                    print values
                    
                    # To submit simply call the function for each value v
                    # v[0] == (metric_name, submit_function)
                    # v[1] == the actual value
                    # FIXME namedtuple probably better here
                    [v[0][1](self, v[0][0], v[1], tags=tags) for v in values]
        finally:
            del cursor

    def get_connection(self, key, host, port, user, password, dbname):
        "Get and memoize connections to instances"
        if key in self.dbs:
            return self.dbs[key]

        elif host != "" and user != "":
            try:
                import psycopg2 as pg
                if host == 'localhost' and password == '':
                    # Use ident method
                    return  pg.connect("user=%s dbname=%s" % (user, dbname))
                elif port != '':
                    return pg.connect(host=host, port=port, user=user,
                                      password=password, database=dbname)
                else:
                    return pg.connect(host=host, user=user, password=password,
                                      database=dbname)
            except ImportError:
                raise ImportError("psycopg2 library can not be imported. Please check the installation instruction on the Datadog Website.")

        else:
            if host is None or host == "":
                raise CheckException("Please specify a Postgres host to connect to.")
            elif user is None or user == "":
                raise CheckException("Please specify a user to connect to Postgres as.")
            else:
                raise CheckException("Cannot connect to Postgres.")


    def check(self, instance):
        host = instance.get('host', '')
        port = instance.get('port', '')
        user = instance.get('username', '')
        password = instance.get('password', '')
        tags = instance.get('tags', [])
        dbname = instance.get('database', 'postgres')
        relations = instance.get('relations', [])
        # Clean up tags in case there was a None entry in the instance
        # e.g. if the yaml contains tags: but no actual tags
        if tags is None:
            tags = []
        key = '%s:%s' % (host, port)

        db = self.get_connection(key, host, port, user, password, dbname)

        # Check version
        version = self._get_version(key, db)
        self.log.debug("Running check against version %s" % version)
            
        # Collect metrics
        self._collect_stats(key, db, tags, relations)

    @staticmethod
    def parse_agent_config(agentConfig):
        server = agentConfig.get('postgresql_server','')
        port = agentConfig.get('postgresql_port','')
        user = agentConfig.get('postgresql_user','')
        passwd = agentConfig.get('postgresql_pass','')

        if server != '' and user != '':
            return {
                'instances': [{
                    'host': server,
                    'port': port,
                    'username': user,
                    'password': passwd
                }]
            }

        return False

if __name__ == '__main__':
    p = PostgreSql("", {}, {})
    p.check({"host": "localhost", "port": 5432, "username": "alq", "password": "", "tags": ["code"]})
