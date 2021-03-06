# File containing the leader class

import sys, os
import subprocess
import time
from threading import Thread, Lock
from socket import SOCK_STREAM, socket, AF_INET, SOL_SOCKET, SO_REUSEADDR
from select import select
from operator import itemgetter
from ast import literal_eval
from replica import Replica
from acceptor import Acceptor

address = 'localhost'
baseport = 20000
n = 0

class Leader(Thread):
	def __init__(self, total, index, address):
		global n, baseport

		Thread.__init__(self)
		self.index   = index
		self.my_port = baseport + self.index*3 + 1

		self.my_sock = socket(AF_INET, SOCK_STREAM)
		self.my_sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)

		n = total
		self.proposals 	= {}
		self.active 	= False
		self.ballot_num = (0, self.index)

		self.crashP1a = (False, [])
		self.crashP2a = (False, [])
		self.crashDecision = (False, [])

	def run(self):
		global n, address

		# Listen for connections
		self.my_sock.bind((address, self.my_port))
		self.my_sock.listen(100*n)

		self.comm_channels = [self.my_sock]

		# Spawn a scout
		scout = Scout(self.index, n, self.ballot_num, self.crashP1a)
		scout.start()
		while(1):
			(active, _, _) = select(self.comm_channels, [], [])

			for sock in active:
				
				if (sock == self.my_sock):
					(newsock, _) = self.my_sock.accept()
					self.comm_channels.append(newsock)
				else:
					# Are we communicating with master, coord, or other servers?
					try:
						line = sock.recv(1024)
					except:
						continue
					#self.send(self.master, "Got here! " + str(self.index))
					if line == '':
						self.comm_channels.remove(sock)

					for data in line.split('\n'):
						if data == '':
							continue

						received = data.strip().split(' ')
						if (received[0] == "propose"):
							# If we already have a mapping for this slot, ignore.
							if not (int(received[1]) in self.proposals.keys()):
								self.proposals[int(received[1])] = (int(received[1]), self.tup(received[2:]))

								# If a majority have adopted this ballot number, send out decision messages.
								if self.active:
									# Spawn Commander.
									commander = Commander(self.index, n, (self.ballot_num, int(received[1]), self.tup(received[2:])), self.crashP2a, self.crashDecision)
									commander.start()
								
						elif (received[0] == "adopted"):
							# Find the max ballot pval for each slot
							updated_proposals = {}

							recv_pvals = self.format_pvals(received[3:])

							for pval in recv_pvals:
								updated_proposals[pval[1]] = 0
							for slot in updated_proposals.keys():
								slot_maps = [pval for pval in recv_pvals if pval[1] == slot]
								max_pval  = max(slot_maps, key=itemgetter(0))

								updated_proposals[max_pval[1]] = (max_pval[1], max_pval[2])

							# Update entries in our proposal table.
							for slot in updated_proposals.keys():
								self.proposals[slot] = updated_proposals[slot]

							# Spawn commanders for all proposals.
							for slot in self.proposals:
								# Spawn commander
								commander = Commander(self.index, n, (self.ballot_num, slot, self.proposals[slot][1]), self.crashP2a, self.crashDecision)
								commander.start()

							self.active = True


						elif (received[0] == "preempted"):
							new_ballot_num = self.tup(received[1:3])
							# Update our ballot number if a larger one is found.
							if (new_ballot_num > self.ballot_num):
								self.active = False
								self.ballot_num = ((int(new_ballot_num[0]) + 1), int(self.index))

								# Spawn a scout.
								scout = Scout(self.index, n, self.ballot_num, self.crashP1a)
								scout.start()

						elif (received[0] == "crashP1a"):
							send_to = received[1:]

							self.crashP1a = (True, send_to)

						elif (received[0] == "crashP2a"):
							send_to = received[1:]

							self.crashP2a = (True, send_to)

						elif (received[0] == "crashDecision"):
							send_to = received[1:]

							self.crashDecision = (True, send_to)

						else:
							pass
		

	def comp_ballots(self, b1, b2):
		if b1[0] < b2[0]:
			return -1
		elif b1[0] > b2[0]:
			return 1
		else:
			if b1[1] < b2[1]:
				return -1
			elif b1[1] > b2[1]:
				return 1
			else:
				return 0


	def send(self, sock, s):
		sock.send(str(s) + '\n')

	def tup(self, sl):
		return literal_eval(' '.join(sl))

	def format_pvals(self, sl):
		ret = []
		for i in range(len(sl)/5):
			ret.append(self.tup(sl[5*i:5*i+5]))

		return ret

	def crash(self):
		# crashes the associated acceptor, replica, and leader
		crashCmd = "ps aux | grep \"src/server.py {}\" | awk '{{print $2}}' | xargs kill".format(self.index)
		subprocess.call(crashCmd, shell=True)




class Scout(Thread):
	def __init__(self, lead_id, n, ballot, crashP1a):
		global address, baseport

		Thread.__init__(self)

		self.leader_id = lead_id
		self.num_acc   = n
		self.b 		   = ballot
		self.timeout   = .01
		self.crashP1a  = crashP1a

		self.wait_for = [i for i in range(n)]
		self.pvalues  = []

		self.acc_sockets = []

		self.lead_sock = socket(AF_INET, SOCK_STREAM)
		self.lead_sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)

		self.lead_sock.connect((address, baseport + self.leader_id*3 + 1))

	def run(self):
		# Broadast p1a messages.
		self.send_p1a(self.crashP1a)

		while(1):
			(active, _, _) = select(self.acc_sockets, [], [], self.timeout)

			# Timeout, resend p1a messages
			if (len(active) == 0):
				self.wait_for 	 = [i for i in range(n)]
				self.pvalues 	 = []
				self.acc_sockets = []

				self.send_p1a(self.crashP1a)


			for sock in active:
				# Loop waiting for responses
				try:
					line = sock.recv(1024)

				except:
					continue

				if line == '':
					self.acc_sockets.remove(sock)

				response = line.strip().split(' ')
				if (response[0] == "p1b"):
					if (self.tup(response[2:4]) == self.b):
						# Add the response to the list of our pvalues.
						pvals = self.format_pvals(response[4:])
						for pval in pvals:
							if not pval in self.pvalues:
								self.pvalues.append(pval)

						# Update wait_for and terminate if we have received a majority
						self.wait_for.remove(int(response[1]))

						if (len(self.wait_for) < self.num_acc/2.0):
							message = "adopted " + str(self.b) + " " + ' '.join([str(pval) for pval in self.pvalues])

							# Send message to leader.
							self.send(self.lead_sock, message)

							for sock in self.acc_sockets:
								sock.close()

							self.lead_sock.close()

							sys.exit()
					# We received a ballot greater than our leaders.
					else:
						message = "preempted " + str((self.tup(response[2:4])))

						# Send to leader.
						self.send(self.lead_sock, message)

						for sock in self.acc_sockets:
							sock.close()

						self.lead_sock.close()

						sys.exit()

				else:
					pass

	def tup(self, sl):
		return literal_eval(' '.join(sl))

	def format_pvals(self, sl):
		ret = []
		for i in range(len(sl)/5):
			ret.append(self.tup(sl[5*i:5*i+5]))

		return ret

	def send(self, sock, s):
		sock.send(str(s) + '\n')

	def send_p1a(self, crashP1a):
		# Connect to all available acceptors.
		for i in range(self.num_acc):
			sock = socket(AF_INET, SOCK_STREAM)
			sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)

			try:
				sock.connect((address, baseport + i*3 + 2))

				self.acc_sockets.append(sock)
			except:
				pass

		# Send a p1a message to all acceptors.
		for i, sock in zip(range(self.num_acc),self.acc_sockets):
			message = "p1a " + str(self.leader_id) + " " + str(self.b)

			# Send message to all acceptors
			if ((not crashP1a[0]) or (crashP1a[0] and i in crashP1a[1])):
				self.send(sock, message)

		if (crashP1a[0]):
			self.crash()

	def crash(self):
		# crashes the associated acceptor, replica, and leader
		crashCmd = "ps aux | grep \"src/server.py {}\" | awk '{{print $2}}' | xargs kill".format(self.leader_id)
		subprocess.call(crashCmd, shell=True)


class Commander(Thread):
	def __init__(self, lead_id, n, pval, crashP2a, crashDecision):
		global address, baseport

		Thread.__init__(self)

		self.leader_id = lead_id
		self.num_acc   = n
		self.timeout   = .01
		self.crashP2a  = crashP2a
		self.crashDecision = crashDecision

		self.wait_for = [i for i in range(n)]
		self.pval = pval

		self.acc_sockets = []
		self.rep_sockets = []

		# Establish connections with all replicas.
		for i in range(self.num_acc):
			sock = socket(AF_INET, SOCK_STREAM)
			sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)

			try:
				sock.connect((address, baseport + i*3 + 0))

				self.rep_sockets.append(sock)
			except:
				pass

		# Connect to out leader
		self.lead_sock = socket(AF_INET, SOCK_STREAM)
		self.lead_sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)

		self.lead_sock.connect((address, baseport + self.leader_id*3 + 1))

	def run(self):
		# Send a p2a message to all acceptors.
		self.send_p2a(self.crashP2a)

		while(1):
			(active, _, _) = select(self.acc_sockets, [], [], self.timeout)

			# Timeout, resend p1a messages
			if (len(active) == 0):
				self.wait_for 	 = [i for i in range(n)]
				self.acc_sockets = []

				self.send_p2a(self.crashP2a)

			for sock in active:
				# Loop waiting for responses
				try:
					line = sock.recv(1024)
				except:
					continue

				if line == '':
					self.acc_sockets.remove(sock)

				response = line.strip().split(' ')
				if (response[0] == "p2b"):
					if (self.tup(response[2:4]) == self.pval[0]):
						# Update wait_for and terminate if we have received a majority
						self.wait_for.remove(int(response[1]))
						if (len(self.wait_for) < self.num_acc/2.0):
							message = "decision " + str(self.pval[1]) + " " + str(self.pval[2])

							# Send message to all replicas.
							for rep_sock in self.rep_sockets:
								if ((not self.crashDecision[0]) or (self.crashDecision[0] and i in self.crashDecision[1])):
									self.send(rep_sock, message)

							if (self.crashDecision[0]):
								self.crash()

							for sock in self.acc_sockets:
								sock.close()

							for sock in self.rep_sockets:
								sock.close()

							self.lead_sock.close()

							sys.exit()
					# We received a ballot greater than our leaders.
					else:
						message = "preempted " + str(self.tup(response[2:4]))

						# Send to leader.
						self.send(self.lead_sock, message)

						for sock in self.acc_sockets:
							sock.close()

						for sock in self.rep_sockets:
							sock.close()

						self.lead_sock.close()

						sys.exit()

				else:
					pass

	def tup(self, sl):
		return literal_eval(' '.join(sl))

	def format_pvals(self, sl):
		ret = []
		for i in range(len(sl)/5):
			ret.append(self.tup(sl[5*i:5*i+5]))

		return ret

	def send(self, sock, s):
		sock.send(str(s) + '\n')

	def send_p2a(self, crashP2a):
		# Establish connections with all acceptors.
		for i in range(self.num_acc):
			sock = socket(AF_INET, SOCK_STREAM)
			sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)

			try:
				sock.connect((address, baseport + i*3 + 2))

				self.acc_sockets.append(sock)
			except:
				pass
		for i, sock in zip(range(self.num_acc),self.acc_sockets):
			message = "p2a " + str(self.leader_id) + " " + str(self.pval)
			# Send message to all acceptors

			if ((not crashP2a[0]) or (crashP2a[0] and i in crashP2a[1])):
				self.send(sock, message)

		if (crashP2a[0]):
			self.crash()

	def crash(self):
		# crashes the associated acceptor, replica, and leader
		crashCmd = "ps aux | grep \"src/server.py {}\" | awk '{{print $2}}' | xargs kill".format(self.leader_id)
		subprocess.call(crashCmd, shell=True)



