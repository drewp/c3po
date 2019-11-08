import sys
import cyclone, cyclone.web
from rdflib import URIRef, Namespace, Variable, Literal
from rdflib import Graph
from twisted.python.util import sibpath
from configs import Bot
from twisted.internet import reactor
from standardservice.logsetup import log, verboseLogging

FOAF = Namespace("http://xmlns.com/foaf/0.1/")
SMS = Namespace("http://bigasterisk.com/ns/sms/")

import smtplib
from email.mime.text import MIMEText

class FoafStore(object):
    """all the getters assume that if we don't know anything about
    your user URI, then you must have passed the right return value
    for the method"""

    def __init__(self, store):
        self.graph = Graph()
        self.graph.parse(store, format="n3")

    def getEmail(self, user):
        e = self.graph.value(URIRef(user), FOAF.mbox, default=user)
        if isinstance(e, URIRef) and e.startswith('mailto:'):
            e = Literal(e[len('mailto:'):])
        return e

    def getJid(self, user):
        return self.graph.value(URIRef(user), FOAF.jabberID, default=user)

    def getSms(self, user):
        """the email address to the SMS gateway for this user's phone.

        I'm looking for this pattern in the RDF store:

            ?user foaf:phone <tel:+555-555-5555> .
            <tel:+555-555-5555> sms:gateway ?gateway .
            ?gateway sms:addressFormat "n@example.com" .

        See http://www.mutube.com/projects/open-email-to-sms/gateway-list/
        for the formats.
        """

        try:
            rows = list(self.graph.query(
                """SELECT ?n ?format WHERE {
                     ?user foaf:phone ?n .
                     ?n sms:gateway [ sms:addressFormat ?format ] .
                   }""",
                initBindings={Variable("user") : URIRef(user)},
                initNs=dict(foaf=FOAF, sms=SMS)))
            if not rows:
                # bug: maybe we know about the user uri, just don't have a
                # phone number!
                raise ValueError("no sms gateway found for %s" % user)

            addr1, addr2 = rows[0][1].split('@')
            n = rows[0][0]
            if not n.startswith("tel:"):
                raise ValueError("unknown phone uri %r" % n)
            n = n[4:].replace('+','').replace('-', '')
            addr = addr1.replace('n', n) + '@' + addr2
        except ValueError:
            addr = user

        return addr

def emailMsg(foaf, to, msg, from_=None, useSubject=True):
    if from_ is None:
        from_ = Bot.senderEmail

    m = MIMEText(msg)
    m['From'] = from_
    m['To'] = foaf.getEmail(to)
    short = msg[:60] + ("..." if len(msg) > 63 else "")
    if useSubject:
        m['Subject'] = '%s%s' % (short, Bot.subjectTag)

    mailServer = smtplib.SMTP(Bot.smtpHost)
    mailServer.sendmail(Bot.senderEmail, [m['To']], m.as_string())
    mailServer.quit()

    return "Mailed %s" % m['To']

def smsMsg(foaf, to, msg, tryJabber=False):
    if tryJabber:
        try:
            xmppMsg(foaf, to, msg, mustBeAvailable=True)
            return "Sent via jabber to %s" % to
        except ValueError:
            pass

    num = foaf.getSms(to)
    emailMsg(foaf, num, msg,
             from_=Bot.senderEmail,
             useSubject=False,
             )
    return "Sent to %s" % num

def xmppMsg(foaf, to, msg, mustBeAvailable=False):
    """
    if mustBeAvailable is set, you'll get an exception if the user
    doesn't have an available presence (so you could fall back on
    other methods)
    """
    jid=xmpp.protocol.JID(Bot.jid)
    cl=xmpp.Client(jid.getDomain(),debug=[])

    # sometimes the next call just prints (!) 'An error occurred while
    # looking up _xmpp-client._tcp.{something.com}'. Then we return
    # successful, even though the connect failed :(
    if not cl.connect((Bot.jabberHost, 5222)):
        raise IOError('Can not connect to server.')
    if not cl.auth(jid.getNode(), Bot.jabberPassword):
        raise IOError('Can not auth with server.')

    cl.send(xmpp.Message(foaf.getJid(to), msg, typ='chat'))
    cl.disconnect()
    return "Jabbered %s" % to

class Root(cyclone.web.RequestHandler):

    def get(self):
        self.render('index.html')

    def post(self):
        user = self.get_argument('user')
        msg = self.get_argument('msg')
        mode = self.get_argument('mode')
        if not user or not msg or not mode:
            raise ValueError("missing user, msg, or mode")

        func = {'email' : emailMsg,
                'xmpp' :  xmppMsg,
                'sms' : smsMsg}[mode]
        self.write(func(self.settings.foaf, user, msg))

if __name__ == "__main__":
    verboseLogging(True)
    foaf = FoafStore(sibpath(__file__, 'accounts.n3'))
    reactor.listenTCP(
        9040,
        cyclone.web.Application([
            (r'/', Root),
        ],
                                template_path='.',
                                foaf=foaf),
        interface='::')
    reactor.run()
