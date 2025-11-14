FROM jgehrcke/github-repo-stats-base:e87aa5891

# Copy all Python scripts
COPY *.py /

# Copy shell scripts and resources
COPY entrypoint.sh /entrypoint.sh
COPY resources /resources

RUN mkdir /rundir && cd /rundir
WORKDIR /rundir
ENTRYPOINT ["/entrypoint.sh"]
