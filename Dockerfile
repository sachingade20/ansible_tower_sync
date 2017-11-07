FROM python:2.7
ADD tower_sync.py /
ADD requirements.txt /
RUN mkdir -p /etc/tower/
RUN pip install -r /requirements.txt
CMD [ "python", "/tower_sync.py" ]
